"""WebSocket connection handler and audio streaming gateway."""

import asyncio
import json
import logging
import time
import uuid
from typing import Dict, Optional
from fastapi import WebSocket, WebSocketDisconnect

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
    """Represents an active client appliance session."""

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

    async def send_json_message(self, model):
        """Send a Pydantic model serialized as JSON text frame."""
        await self.websocket.send_text(model.model_dump_json())

    async def send_state(self, state: JarvisState, message: Optional[str] = None):
        """Transition and broadcast state change to client."""
        self.state = state
        await self.send_json_message(StateChange(state=state, message=message))

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
            logger.info("Voice start signal received from client.")
            self.is_recording = True
            self.audio_buffer.clear()
            await self.send_state(JarvisState.LISTENING)

        elif msg_type == "voice_end":
            logger.info(f"Voice end signal. Total audio buffer: {len(self.audio_buffer)} bytes.")
            self.is_recording = False
            await self.process_recorded_audio()

        elif msg_type == "ping":
            ts = payload.get("timestamp", int(time.time() * 1000))
            await self.send_json_message(PongMessage(timestamp=ts))

    async def handle_binary_frame(self, data_bytes: bytes):
        """Handle incoming raw audio PCM chunks."""
        if not self.is_recording:
            # Auto-detect speech if client streams continuously
            if self.vad.is_speech(data_bytes):
                self.is_recording = True
                self.audio_buffer.clear()
                await self.send_state(JarvisState.LISTENING)

        if self.is_recording:
            self.audio_buffer.extend(data_bytes)
            # Emit telemetry for user's voice
            amp = self.vad.get_normalized_amplitude(data_bytes)
            await self.send_json_message(
                VisualTelemetry(amplitude=amp, frequency_bands=[amp * 0.8, amp, amp * 0.6])
            )

    async def process_recorded_audio(self):
        """Process buffered speech through STT -> Hermes -> TTS."""
        if len(self.audio_buffer) < 3200:  # Less than 100ms
            logger.info("Audio buffer too short, reverting to IDLE.")
            await self.send_state(JarvisState.IDLE)
            return

        captured_pcm = bytes(self.audio_buffer)
        self.audio_buffer.clear()

        # Step 1: Thinking / Transcribing
        await self.send_state(JarvisState.THINKING)
        transcription = await self.stt.transcribe(captured_pcm)
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

        # Step 3: Speaking / Streaming TTS
        await self.send_state(JarvisState.SPEAKING)
        await self.send_json_message(TTSStart())

        async for chunk in self.tts.synthesize_stream(agent_reply):
            # Send audio binary chunk
            await self.websocket.send_bytes(chunk)
            # Send simulated audio telemetry
            amp = self.vad.get_normalized_amplitude(chunk)
            await self.send_json_message(
                VisualTelemetry(
                    amplitude=max(0.2, amp),
                    frequency_bands=[amp * 0.9, amp * 1.1, amp * 0.7],
                )
            )

        await self.send_json_message(TTSEnd())
        await self.send_state(JarvisState.IDLE)
