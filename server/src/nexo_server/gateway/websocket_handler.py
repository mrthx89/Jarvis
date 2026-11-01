"""WebSocket connection handler and audio streaming gateway for Nexo Appliance."""

import asyncio
import json
import logging
import time
from typing import Any, Optional
from fastapi import WebSocket

from ..models.messages import (
    ServerHello,
    StateChange,
    VisualTelemetry,
    TranscriptionMessage,
    TTSStart,
    TTSEnd,
    PongMessage,
    NexoState,
)
from ..pipeline.vad import EnergyVAD
from ..pipeline.stt import BaseSTT
from ..pipeline.tts import BaseTTS
from ..hermes.agent_bridge import HermesBridge
from ..mascot import (
    contextual_expression,
    parse_expression_command,
    parse_and_strip_emotional_tags,
    strip_emotional_tags,
)
from .audio_buffer import CircularAudioBuffer, CircularRingBuffer
from .mp3_frame import parse_audio_duration, parse_mp3_duration
from ..hermes.sentence_chunker import split_into_sentences

logger = logging.getLogger(__name__)


class ProtocolViolation(ValueError):
    """Safe fixed diagnostic; never includes user input."""


class ApplianceSession:
    """Half-duplex voice session: controls stay live while audio is processed."""

    PROCESSING_TIMEOUT = 120.0
    SEND_TIMEOUT = 5.0
    AUDIO_GAP_TIMEOUT = 2.0
    SILENCE_BYTES = 48000  # 1500 ms PCM16 mono at 16 kHz
    MAX_AUDIO_BYTES = 30 * 32000
    MAX_FRAME_BYTES = 32000 * 2
    PREROLL_BYTES = 8000  # 250 ms at 16 kHz, bounded even in hours of silence
    ECHO_GUARD_SECONDS = 0.6  # Preserve echo protection, only after speaker output.

    def __init__(
        self,
        websocket: WebSocket,
        session_id: str,
        stt: BaseSTT,
        tts: BaseTTS,
        hermes: HermesBridge,
        authenticated_device_id: str | None = None,
        max_json_bytes: int = 4096,
        agent_name: str = "Nexo",
        session_hub: Optional[Any] = None,
    ):
        self.authenticated_device_id = authenticated_device_id
        self.agent_name = agent_name
        self.ready = False
        self.max_json_bytes = max_json_bytes
        self.websocket = websocket
        self.session_id = session_id
        self.stt = stt
        self.tts = tts
        self.hermes = hermes
        self.hub = session_hub
        self.vad = EnergyVAD()
        self.state = NexoState.IDLE
        self.audio_buffer = bytearray()
        self.is_recording = False
        self._silence_bytes = 0
        self._processing_task = None
        self._closed = False
        self._cooldown_until = 0.0
        self._playback_until = 0.0
        self._tts_started = False
        self._external_speech = False
        self._gap_handle = None
        self._preroll = CircularAudioBuffer(capacity=self.PREROLL_BYTES)
        if self.hub:
            self.hub.register_session(self)

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
        if self.hub:
            self.hub.unregister_session(self)
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
        return self._closed or self._external_speech or time.monotonic() < self._cooldown_until or (
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
        state: NexoState,
        message: Optional[str] = None,
        expression: Optional[str] = None,
    ):
        """Transition and broadcast state change with mascot expression to client."""
        self.state = state
        if self.hub:
            state_val = getattr(state, "value", str(state))
            self.hub.publish_event({
                "type": "state_change",
                "state": state_val,
                "message": message,
                "expression": expression,
            })
        await self.send_json_message(
            StateChange(state=state, message=message, expression=expression)
        )

    async def _resume_listening(self):
        """Advertise readiness only after estimated speaker drain and echo guard.

        Empty STT produces no audio and must not create a deaf cooldown.
        The Android client independently waits for actual AudioTrack drain.
        """
        if self._closed:
            return
        if self._playback_until:
            self._cooldown_until = max(time.monotonic(), self._playback_until) + self.ECHO_GUARD_SECONDS
            await asyncio.sleep(max(0.0, self._cooldown_until - time.monotonic()))
        self._playback_until = 0.0
        self._cooldown_until = 0.0
        self._preroll.clear()
        await self.send_state(NexoState.IDLE, expression="netral")

    async def speak_text(self, text: str, expression: Optional[str] = None) -> bool:
        """Stream TTS audio to the appliance speaker and animate the mascot."""
        if self._busy() or not self.ready or self.is_recording:
            return False
        self._external_speech = True
        try:
            tag_expr, clean_text = parse_and_strip_emotional_tags(text)
            resolved_expr = tag_expr.value if tag_expr else (expression or "senang")
            await self.send_json_message(
                TranscriptionMessage(role="assistant", text=clean_text, is_final=True)
            )
            return await self._speak_text_owned(clean_text, resolved_expr)
        finally:
            self._external_speech = False

    async def _speak_text_owned(self, text: str, expression: Optional[str]) -> bool:
        tag_expr, clean_text = parse_and_strip_emotional_tags(text)
        speech_expr = tag_expr.value if tag_expr else (expression or "senang")
        await self.send_state(NexoState.SPEAKING, expression=speech_expr)
        await self.send_json_message(TTSStart())
        self._tts_started = True

        try:
            async for chunk in self.tts.synthesize_stream(clean_text):
                await asyncio.wait_for(self.websocket.send_bytes(chunk), self.SEND_TIMEOUT)
                self._playback_until = max(time.monotonic(), self._playback_until) + parse_audio_duration(chunk)
                amp = self.vad.get_normalized_amplitude(chunk)
                await self.send_json_message(
                    VisualTelemetry(
                        amplitude=max(0.2, amp),
                        frequency_bands=[amp * 0.9, amp * 1.1, amp * 0.7],
                    )
                )
            return True
        finally:
            if self._tts_started:
                try:
                    await self.send_json_message(TTSEnd())
                except Exception:
                    pass
                self._tts_started = False
            try:
                await self._resume_listening()
            except Exception:
                pass

    async def handle_text_frame(self, data_str: str):
        """Handle incoming control JSON frames."""
        if self._closed:
            return
        if not self.authenticated_device_id:
            raise ProtocolViolation("Unauthenticated session")
        if len(data_str.encode("utf-8")) > self.max_json_bytes:
            raise ProtocolViolation("Control frame too large")
        try:
            payload = json.loads(data_str)
        except (ValueError, RecursionError):
            raise ProtocolViolation("Invalid control JSON") from None
        if not isinstance(payload, dict):
            raise ProtocolViolation("Control frame must be an object")
        msg_type = payload.get("type")
        if not self.ready and msg_type != "client_hello":
            raise ProtocolViolation("Hello required")

        if msg_type == "client_hello":
            if self.ready:
                raise ProtocolViolation("Duplicate hello")
            expected_audio = {"format": "audio/pcm", "sample_rate": 16000, "channels": 1, "bit_depth": 16}
            if (payload.get("device_id") != self.authenticated_device_id
                    or payload.get("protocol_version") != "1.0"
                    or payload.get("audio_spec") != expected_audio
                    or not isinstance(payload.get("audio_spec"), dict)
                    or any(type(payload["audio_spec"].get(k)) is not type(v) for k, v in expected_audio.items())):
                raise ProtocolViolation("Unsupported hello identity or audio protocol")
            self.ready = True
            logger.info("Protocol ready for session %s", self.session_id)
            await self.send_json_message(
                ServerHello(
                    session_id=self.session_id,
                    agent_name=self.agent_name,
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
            await self.send_state(NexoState.LISTENING, expression="terkejut")

        elif msg_type == "voice_end":
            await self._finish_recording()

        elif msg_type == "ping":
            ts = payload.get("timestamp", int(time.time() * 1000))
            if type(ts) is not int or not 0 <= ts <= 2**63 - 1:
                raise ProtocolViolation("Invalid ping")
            await self.send_json_message(PongMessage(timestamp=ts))
        else:
            raise ProtocolViolation("Unknown control type")

    async def handle_binary_frame(self, data_bytes: bytes):
        """Handle incoming raw audio PCM chunks."""
        if not self.ready or not self.authenticated_device_id:
            # No PCM may reach VAD, buffering or providers before auth + hello.
            raise ProtocolViolation("Hello required before audio")
        if self._busy():
            self._preroll.clear()
            return  # Half duplex: never queue speaker echo for the next turn.
        if not data_bytes or len(data_bytes) % 2 or len(data_bytes) > self.MAX_FRAME_BYTES:
            logger.warning("Dropping invalid PCM frame (%d bytes)", len(data_bytes))
            return
        speech = self.vad.is_speech(data_bytes, continuing=self.is_recording)
        if not self.is_recording:
            if not speech:
                self._preroll.extend(data_bytes)
                return
            self.is_recording = True
            self._silence_bytes = 0
            self.audio_buffer.clear()
            self.audio_buffer.extend(self._preroll.get_bytes())
            self._preroll.clear()
            await self.send_state(NexoState.LISTENING)

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
            logger.error("Voice pipeline failed for %s", self.session_id)
            if not self._closed:
                try:
                    await self.send_state(
                        NexoState.ERROR,
                        message="Pemrosesan suara gagal atau melebihi batas waktu. Silakan coba lagi.",
                        expression="sedih",
                    )
                except Exception:
                    logger.debug("Unable to send pipeline error")
        finally:
            if not self._closed:
                try:
                    if self._tts_started:
                        await self.send_json_message(TTSEnd())
                        self._tts_started = False
                    await self._resume_listening()
                except Exception:
                    logger.debug("Unable to send idle state")

    async def _run_pipeline(self, captured_pcm):
        """Process STT -> local mascot command or Hermes -> TTS."""
        # Step 1: Thinking / Transcribing
        await self.send_state(NexoState.THINKING, expression="bingung")
        transcription = (await self.stt.transcribe(captured_pcm)).strip()
        if not transcription:
            logger.info("No speech transcribed for %s", self.session_id)
            return
        logger.info("STT complete for %s (%d characters)", self.session_id, len(transcription))

        # Send transcription subtitle
        await self.send_json_message(
            TranscriptionMessage(role="user", text=transcription, is_final=True)
        )
        if self.hub:
            self.hub.publish_event({
                "type": "transcription",
                "role": "user",
                "text": transcription,
                "source": "appliance_voice",
            })

        # Step 2: Only a whole supported mascot command bypasses Hermes.
        command_expression = parse_expression_command(transcription)
        if command_expression is not None:
            agent_reply = "Baik, ekspresi wajah sudah diubah."
            speech_expr = command_expression.value
            await self.send_json_message(
                TranscriptionMessage(role="assistant", text=agent_reply, is_final=True)
            )
            if self.hub:
                self.hub.publish_event({
                    "type": "transcription",
                    "role": "assistant",
                    "text": agent_reply,
                    "expression": speech_expr,
                    "source": "nexo_hermes",
                })
            # Speaking / Streaming TTS
            await self.send_state(NexoState.SPEAKING, expression=speech_expr)
            await self.send_json_message(TTSStart())
            self._tts_started = True

            async for chunk in self.tts.synthesize_stream(agent_reply):
                await asyncio.wait_for(self.websocket.send_bytes(chunk), self.SEND_TIMEOUT)
                self._playback_until = max(time.monotonic(), self._playback_until) + parse_audio_duration(chunk)
                amp = self.vad.get_normalized_amplitude(chunk)
                await self.send_json_message(
                    VisualTelemetry(
                        amplitude=max(0.2, amp),
                        frequency_bands=[amp * 0.9, amp * 1.1, amp * 0.7],
                    )
                )
            return

        # Step 3: Speaking / Streaming TTS from Hermes
        if hasattr(self.hermes, "query_stream"):
            collected_sentences = []
            speech_expr = None
            async for sentence in self.hermes.query_stream(transcription):
                clean_sentence = sentence.strip()
                if not clean_sentence:
                    continue
                tag_expr, clean_sentence = parse_and_strip_emotional_tags(clean_sentence)
                if not clean_sentence:
                    continue
                collected_sentences.append(clean_sentence)

                if tag_expr:
                    speech_expr = tag_expr.value
                    await self.send_state(NexoState.SPEAKING, expression=speech_expr)
                elif speech_expr is None:
                    speech_expr = contextual_expression(transcription, clean_sentence).value
                    await self.send_state(NexoState.SPEAKING, expression=speech_expr)

                if not self._tts_started:
                    await self.send_json_message(TTSStart())
                    self._tts_started = True

                if self.hub:
                    self.hub.publish_event({
                        "type": "cognitive",
                        "stage": "streaming",
                        "sentence": clean_sentence,
                        "content": clean_sentence,
                        "index": len(collected_sentences) - 1,
                        "expression": speech_expr,
                    })

                async for chunk in self.tts.synthesize_stream(clean_sentence):
                    await asyncio.wait_for(self.websocket.send_bytes(chunk), self.SEND_TIMEOUT)
                    self._playback_until = max(time.monotonic(), self._playback_until) + parse_audio_duration(chunk)
                    amp = self.vad.get_normalized_amplitude(chunk)
                    await self.send_json_message(
                        VisualTelemetry(
                            amplitude=max(0.2, amp),
                            frequency_bands=[amp * 0.9, amp * 1.1, amp * 0.7],
                        )
                    )

            full_reply = " ".join(collected_sentences).strip()
            if full_reply:
                logger.info("Agent complete for %s (%d characters)", self.session_id, len(full_reply))
                await self.send_json_message(
                    TranscriptionMessage(role="assistant", text=full_reply, is_final=True)
                )
                if self.hub:
                    self.hub.publish_event({
                        "type": "transcription",
                        "role": "assistant",
                        "text": full_reply,
                        "expression": speech_expr or "netral",
                        "source": "nexo_hermes",
                    })
        else:
            agent_reply = await self.hermes.query(transcription)
            tag_expr, clean_reply = parse_and_strip_emotional_tags(agent_reply)
            speech_expr = tag_expr.value if tag_expr else contextual_expression(transcription, clean_reply).value
            logger.info("Agent complete for %s (%d characters)", self.session_id, len(clean_reply))

            await self.send_json_message(
                TranscriptionMessage(role="assistant", text=clean_reply, is_final=True)
            )
            if self.hub:
                self.hub.publish_event({
                    "type": "transcription",
                    "role": "assistant",
                    "text": clean_reply,
                    "expression": speech_expr,
                    "source": "nexo_hermes",
                })

            await self.send_state(NexoState.SPEAKING, expression=speech_expr)
            await self.send_json_message(TTSStart())
            self._tts_started = True

            sentences = split_into_sentences(clean_reply)
            if not sentences:
                sentences = [clean_reply]

            for sentence in sentences:
                async for chunk in self.tts.synthesize_stream(sentence):
                    await asyncio.wait_for(self.websocket.send_bytes(chunk), self.SEND_TIMEOUT)
                    self._playback_until = max(time.monotonic(), self._playback_until) + parse_audio_duration(chunk)
                    amp = self.vad.get_normalized_amplitude(chunk)
                    await self.send_json_message(
                        VisualTelemetry(
                            amplitude=max(0.2, amp),
                            frequency_bands=[amp * 0.9, amp * 1.1, amp * 0.7],
                        )
                    )
