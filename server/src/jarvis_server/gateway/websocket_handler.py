"""WebSocket connection handler and audio streaming gateway."""

import asyncio
import json
import logging
import time
from typing import Optional
from fastapi import WebSocket

from ..models.messages import (
    ClientHello,
    ServerHello,
    StateChange,
    VisualTelemetry,
    TranscriptionMessage,
    TTSStart,
    TTSEnd,
    PongMessage,
    JarvisState,
)
from ..pipeline.vad import EnergyVAD
from ..pipeline.stt import BaseSTT
from ..pipeline.tts import BaseTTS
from ..hermes.agent_bridge import HermesBridge

logger = logging.getLogger(__name__)


class ApplianceSession:
    """Half-duplex voice session: controls stay live while audio is processed."""

    PROCESSING_TIMEOUT = 120.0
    SEND_TIMEOUT = 5.0
    AUDIO_GAP_TIMEOUT = 1.2
    SILENCE_BYTES = 25600  # 800 ms PCM16 mono at 16 kHz
    MAX_AUDIO_BYTES = 30 * 32000
    MAX_FRAME_BYTES = 32000 * 2
    PREROLL_BYTES = 8000  # 250 ms at 16 kHz, bounded even in hours of silence

    def __init__(
        self,
        websocket: WebSocket,
        session_id: str,
        stt: BaseSTT,
        tts: BaseTTS,
        hermes: HermesBridge,
    ):
        self.websocket = websocket
        self.session_id = session_id
        self.stt = stt
        self.tts = tts
        self.hermes = hermes
        self.vad = EnergyVAD()
        self.state = JarvisState.IDLE
        self.audio_buffer = bytearray()
        self.is_recording = False
        self._silence_bytes = 0
        self._processing_task = None
        self._closed = False
        self._cooldown_until = 0.0
        self._playback_until = 0.0
        self._tts_started = False
        self._gap_handle = None
        self._preroll = bytearray()

    def _arm_gap_timeout(self):
        if self._gap_handle:
            self._gap_handle.cancel()
        self._gap_handle = asyncio.get_running_loop().call_later(
            self.AUDIO_GAP_TIMEOUT, self._on_audio_gap
        )

    def _on_audio_gap(self):
        self._gap_handle = None
        if self.is_recording and not self._busy():
            self.is_recording = False
            self._processing_task = asyncio.create_task(self.process_recorded_audio())

    async def close(self):
        self._closed = True
        if self._gap_handle:
            self._gap_handle.cancel()
            self._gap_handle = None
        if self._processing_task:
            self._processing_task.cancel()
            await asyncio.gather(self._processing_task, return_exceptions=True)
        self.audio_buffer.clear()
        self._preroll.clear()
        self.is_recording = False

    def _busy(self):
        return self._closed or time.monotonic() < self._cooldown_until or (
            self._processing_task is not None and not self._processing_task.done()
        )

    async def _finish_recording(self):
        if self._busy() or not self.is_recording:
            return
        self.is_recording = False
        if self._gap_handle:
            self._gap_handle.cancel()
            self._gap_handle = None
        self._processing_task = asyncio.create_task(self.process_recorded_audio())

    async def send_json_message(self, model):
        """Send a Pydantic model serialized as JSON text frame."""
        await asyncio.wait_for(
            self.websocket.send_text(model.model_dump_json()), self.SEND_TIMEOUT
        )

    async def send_state(
        self,
        state: JarvisState,
        message: Optional[str] = None,
        expression: Optional[str] = None,
    ):
        """Transition and broadcast state change with mascot expression to client."""
        self.state = state
        await self.send_json_message(
            StateChange(state=state, message=message, expression=expression)
        )

    async def handle_text_frame(self, data_str: str):
        """Handle incoming control JSON frames."""
        try:
            payload = json.loads(data_str)
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON received: {data_str}")
            return

        msg_type = payload.get("type")

        if msg_type == "client_hello":
            hello = ClientHello(**payload)
            logger.info(f"Client authenticated: {hello.device_id}")
            await self.send_json_message(
                ServerHello(
                    session_id=self.session_id,
                    agent_name="Jarvis",
                    status=self.state,
                )
            )

        elif msg_type == "voice_start":
            if self._busy():
                return
            self.is_recording = True
            self._silence_bytes = 0
            self.audio_buffer.clear()
            self._preroll.clear()
            self._arm_gap_timeout()
            await self.send_state(JarvisState.LISTENING, expression="terkejut")

        elif msg_type == "voice_end":
            await self._finish_recording()

        elif msg_type == "ping":
            ts = payload.get("timestamp", int(time.time() * 1000))
            await self.send_json_message(PongMessage(timestamp=ts))

    async def handle_binary_frame(self, data_bytes: bytes):
        """Handle incoming raw audio PCM chunks."""
        if self._busy():
            self._preroll.clear()
            return  # Half duplex: never queue speaker echo for the next turn.
        if not data_bytes or len(data_bytes) % 2 or len(data_bytes) > self.MAX_FRAME_BYTES:
            logger.warning("Dropping invalid PCM frame (%d bytes)", len(data_bytes))
            return
        speech = self.vad.is_speech(data_bytes)
        if not self.is_recording:
            if not speech:
                self._preroll.extend(data_bytes)
                del self._preroll[:-self.PREROLL_BYTES]
                return
            self.is_recording = True
            self._silence_bytes = 0
            self.audio_buffer.clear()
            self.audio_buffer.extend(self._preroll)
            self._preroll.clear()
            await self.send_state(JarvisState.LISTENING)

        if self.is_recording:
            self._arm_gap_timeout()
            remaining = self.MAX_AUDIO_BYTES - len(self.audio_buffer)
            self.audio_buffer.extend(data_bytes[:remaining])
            self._silence_bytes = 0 if speech else self._silence_bytes + len(data_bytes)
            amp = self.vad.get_normalized_amplitude(data_bytes)
            await self.send_json_message(
                VisualTelemetry(amplitude=amp, frequency_bands=[amp * 0.8, amp, amp * 0.6])
            )
            if self._silence_bytes >= self.SILENCE_BYTES or len(self.audio_buffer) >= self.MAX_AUDIO_BYTES:
                await self._finish_recording()

    async def process_recorded_audio(self):
        """Own one turn; bound provider waits and always release session state."""
        captured_pcm = bytes(self.audio_buffer)
        self.audio_buffer.clear()
        try:
            async with asyncio.timeout(self.PROCESSING_TIMEOUT):
                if len(captured_pcm) >= 3200:
                    await self._run_pipeline(captured_pcm)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Voice pipeline failed for %s", self.session_id)
            if not self._closed:
                try:
                    await self.send_state(
                        JarvisState.ERROR,
                        message="Pemrosesan suara gagal atau melebihi batas waktu. Silakan coba lagi.",
                        expression="sedih",
                    )
                except Exception:
                    logger.debug("Unable to send pipeline error", exc_info=True)
        finally:
            # PCM may be sent faster than real-time. Suppress microphone echo
            # until the client's queued samples finish, plus acoustic tail.
            self._cooldown_until = max(time.monotonic(), self._playback_until) + 0.6
            if not self._closed:
                try:
                    if self._tts_started:
                        await self.send_json_message(TTSEnd())
                        self._tts_started = False
                    await self.send_state(JarvisState.IDLE, expression="netral")
                except Exception:
                    logger.debug("Unable to send idle state", exc_info=True)

    async def _run_pipeline(self, captured_pcm):
        """Process captured speech through STT -> Hermes -> TTS."""

        # Step 1: Thinking / Transcribing
        await self.send_state(JarvisState.THINKING, expression="bingung")
        transcription = (await self.stt.transcribe(captured_pcm)).strip()
        if not transcription:
            logger.info("No speech transcribed for %s", self.session_id)
            return
        logger.info(f"Transcription: {transcription}")


        # Send transcription subtitle
        await self.send_json_message(
            TranscriptionMessage(role="user", text=transcription, is_final=True)
        )

        # Step 2: Query Hermes Agent
        agent_reply = await self.hermes.query(transcription)
        logger.info(f"Agent response: {agent_reply}")

        await self.send_json_message(
            TranscriptionMessage(role="assistant", text=agent_reply, is_final=True)
        )

        # Step 3: Speaking / Streaming TTS with appropriate emotional expression
        reply_lower = agent_reply.lower()
        if any(w in reply_lower for w in ["haha", "lucu", "wkwk"]):
            speech_expr = "tertawa"
        elif any(w in reply_lower for w in ["luar biasa", "selamat", "berhasil", "hebat"]):
            speech_expr = "sangat_senang"
        elif any(w in reply_lower for w in ["siap", "tentu", "aman"]):
            speech_expr = "keren"
        elif any(w in reply_lower for w in ["maaf", "kendala", "gagal"]):
            speech_expr = "sedih"
        else:
            speech_expr = "senang"

        await self.send_state(JarvisState.SPEAKING, expression=speech_expr)
        await self.send_json_message(TTSStart())
        self._tts_started = True

        async for chunk in self.tts.synthesize_stream(agent_reply):
            await asyncio.wait_for(self.websocket.send_bytes(chunk), self.SEND_TIMEOUT)
            self._playback_until = max(time.monotonic(), self._playback_until) + len(chunk) / 48000.0
            amp = self.vad.get_normalized_amplitude(chunk)
            await self.send_json_message(
                VisualTelemetry(
                    amplitude=max(0.2, amp),
                    frequency_bands=[amp * 0.9, amp * 1.1, amp * 0.7],
                )
            )

        # TTSEnd and IDLE are emitted by the turn's finally block, including
        # interrupted synthesis, so the client never gets stuck in SPEAKING.
