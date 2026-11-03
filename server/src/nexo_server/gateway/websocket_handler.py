"""WebSocket connection handler and audio streaming gateway for Nexo Appliance."""

import asyncio
import json
import logging
import time
from collections import deque
from contextlib import aclosing
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
    VoiceprintDialog,
    NexoState,
)
from ..pipeline.vad import EnergyVAD
from ..pipeline.stt import BaseSTT
from ..pipeline.tts import BaseTTS, prefetch_tts
from ..hermes.agent_bridge import HermesBridge
from ..mascot import (
    contextual_expression,
    parse_expression_command,
    parse_and_strip_emotional_tags,
    strip_emotional_tags,
    is_sleep_command,
    is_wake_command,
    MascotExpression,
)
from .audio_buffer import CircularAudioBuffer, CircularRingBuffer
from .mp3_frame import parse_audio_duration, parse_mp3_duration
from ..hermes.sentence_chunker import split_into_sentences
from ..pipeline.speaker import (
    SpeakerVerifier,
    is_voice_enroll_command,
    is_voice_unenroll_command,
)

logger = logging.getLogger(__name__)


class ProtocolViolation(ValueError):
    """Safe fixed diagnostic; never includes user input."""


class ApplianceSession:
    """Half-duplex voice session: controls stay live while audio is processed."""

    PROCESSING_TIMEOUT = 120.0
    SEND_TIMEOUT = 5.0
    AUDIO_GAP_TIMEOUT = 2.0
    SILENCE_BYTES = 32000  # 1000 ms PCM16 mono at 16 kHz (calibrated fast endpointing)
    MAX_AUDIO_BYTES = 30 * 32000
    MAX_FRAME_BYTES = 32000 * 2
    PREROLL_BYTES = 8000  # 250 ms at 16 kHz, bounded even in hours of silence
    ECHO_GUARD_SECONDS = 0.6  # Preserve echo protection, only after speaker output.
    MAX_PLAYBACK_AHEAD = 1.0
    ENROLL_TIMEOUT = 15.0
    ENROLL_MIN_BYTES = 64000
    ENROLL_MAX_BYTES = 192000

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
        speaker_verifier: Optional[Any] = None,
        vad_threshold_db: float = -38.0,
        silence_timeout_seconds: float = 1.0,
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
        self.speaker_verifier = speaker_verifier
        self.vad = EnergyVAD(threshold_db=vad_threshold_db)
        self.silence_bytes = int(silence_timeout_seconds * 32000)
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
        self._enrollment = None
        self._enroll_request_ids = set()
        self._enroll_task = None
        self._enroll_worker = None
        self._enroll_deadline_task = None
        self._gap_handle = None
        self._preroll = CircularAudioBuffer(capacity=self.PREROLL_BYTES)
        self.is_sleeping = False
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

    def _clear_enrollment(self):
        if self._enrollment is not None:
            self._enrollment["pcm"].clear()
        self._enrollment = None
        current = asyncio.current_task()
        for task in (self._enroll_task, self._enroll_deadline_task):
            if task and task is not current:
                task.cancel()
        self._enroll_task = None
        self._enroll_deadline_task = None
        self.audio_buffer.clear()
        self._preroll.clear()
        self.is_recording = False

    async def _voiceprint_reply(self, action, request_id=None, success=None, message=None):
        verifier = self.speaker_verifier
        await self.send_json_message(VoiceprintDialog(
            action=action, request_id=request_id, success=success, message=message,
            available=bool(verifier and verifier.is_available()),
            is_enrolled=bool(verifier and verifier.is_enrolled("owner")),
            metadata=verifier.get_metadata("owner") if verifier else None,
        ))

    async def _enrollment_deadline(self, enrollment):
        await asyncio.sleep(self.ENROLL_TIMEOUT)
        if self._enrollment is enrollment:
            self._clear_enrollment()
            await self._voiceprint_reply("error", enrollment["request_id"], False,
                                         "Waktu pendaftaran habis. Silakan coba lagi.")

    @staticmethod
    def _consume_enrollment_worker(task):
        if not task.cancelled():
            task.exception()

    async def _complete_enrollment(self, enrollment):
        request_id = enrollment["request_id"]
        captured = bytes(enrollment["pcm"])
        enrollment["pcm"].clear()
        verifier = self.speaker_verifier
        assert verifier is not None
        try:
            if len(captured) < self.ENROLL_MIN_BYTES:
                ok, message = False, "Durasi rekaman terlalu pendek. Bicara minimal 2 detik."
            else:
                # Only extraction runs off-thread; a cancelled worker cannot write a profile.
                self._enroll_worker = asyncio.create_task(asyncio.to_thread(verifier.extract_embedding, captured))
                self._enroll_worker.add_done_callback(self._consume_enrollment_worker)
                embedding = await asyncio.shield(self._enroll_worker)
                if self._enrollment is not enrollment or self._closed:
                    return
                if time.monotonic() >= enrollment["deadline"]:
                    ok, message = False, "Waktu pendaftaran habis. Silakan coba lagi."
                elif embedding is None:
                    ok, message = False, "Suara tidak terdengar jelas atau model tidak tersedia."
                else:
                    ok, message = verifier.save_embedding(embedding, len(captured), "owner")
            if self._enrollment is enrollment:
                self._clear_enrollment()
                await self._voiceprint_reply("enrolled" if ok else "error", request_id, ok, message)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Voiceprint enrollment failed")
            if self._enrollment is enrollment:
                self._clear_enrollment()
                await self._voiceprint_reply("error", request_id, False, "Pendaftaran sidik suara gagal.")

    async def _handle_voiceprint_control(self, payload):
        kind = payload["type"]
        request_id = payload.get("request_id")
        if (kind != "voiceprint_query" or request_id is not None) and (
            not isinstance(request_id, str) or not 1 <= len(request_id) <= 128
            or not request_id.strip()
        ):
            raise ProtocolViolation("Invalid voiceprint request ID")
        if kind == "voiceprint_query":
            await self._voiceprint_reply("query", request_id)
        elif kind == "voiceprint_enroll_start":
            if request_id in self._enroll_request_ids or len(self._enroll_request_ids) >= 1024:
                await self._voiceprint_reply("error", request_id, False, "Gunakan ID pendaftaran baru atau sambungkan ulang.")
                return
            if (self._busy() or self.is_recording
                    or (self._enroll_worker is not None and not self._enroll_worker.done())):
                await self._voiceprint_reply("error", request_id, False, "Sesi sedang sibuk.")
                return
            if not self.speaker_verifier or not self.speaker_verifier.is_available():
                await self._voiceprint_reply("error", request_id, False, "Model sidik suara tidak tersedia.")
                return
            if self._gap_handle:
                self._gap_handle.cancel()
                self._gap_handle = None
            self.audio_buffer.clear()
            self._preroll.clear()
            enrollment = {"request_id": request_id, "pcm": bytearray(), "finishing": False,
                          "deadline": time.monotonic() + self.ENROLL_TIMEOUT}
            self._enrollment = enrollment
            self._enroll_request_ids.add(request_id)
            self._enroll_deadline_task = asyncio.create_task(self._enrollment_deadline(enrollment))
            await self._voiceprint_reply("recording", request_id, message="Bicara selama 2 hingga 6 detik.")
        elif kind in ("voiceprint_enroll_finish", "voiceprint_enroll_cancel"):
            enrollment = self._enrollment
            if enrollment is None or enrollment["request_id"] != request_id:
                await self._voiceprint_reply("error", request_id, False, "Pendaftaran tidak aktif.")
                return
            if kind == "voiceprint_enroll_cancel":
                self._clear_enrollment()
                await self._voiceprint_reply("cancelled", request_id, False, "Pendaftaran dibatalkan.")
            elif not enrollment["finishing"]:
                enrollment["finishing"] = True
                self._enroll_task = asyncio.create_task(self._complete_enrollment(enrollment))
        elif kind == "voiceprint_unenroll":
            if self._busy() or self.is_recording:
                await self._voiceprint_reply("unenroll", request_id, False, "Sesi sedang sibuk.")
                return
            if self.speaker_verifier:
                ok, message = self.speaker_verifier.unenroll("owner")
            else:
                ok, message = False, "Modul sidik suara tidak aktif."
            await self._voiceprint_reply("unenroll", request_id, ok, message)

    async def close(self):
        self._closed = True
        self._clear_enrollment()
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
        return self._closed or self._enrollment is not None or self._external_speech or time.monotonic() < self._cooldown_until or (
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
        """Send a Pydantic model or dict serialized as JSON text frame."""
        text = model.model_dump_json() if hasattr(model, "model_dump_json") else json.dumps(model)
        await asyncio.wait_for(
            self.websocket.send_text(text), self.SEND_TIMEOUT
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
        target_expr = "sleeping" if self.is_sleeping else "netral"
        await self.send_state(NexoState.IDLE, expression=target_expr)

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

        elif msg_type in ("voiceprint_query", "voiceprint_unenroll", "voiceprint_enroll_start",
                          "voiceprint_enroll_finish", "voiceprint_enroll_cancel"):
            await self._handle_voiceprint_control(payload)
        else:
            raise ProtocolViolation("Unknown control type")

    async def handle_binary_frame(self, data_bytes: bytes):
        """Handle incoming raw audio PCM chunks."""
        if not self.ready or not self.authenticated_device_id:
            # No PCM may reach VAD, buffering or providers before auth + hello.
            raise ProtocolViolation("Hello required before audio")
        if self._enrollment is not None:
            enrollment = self._enrollment
            if self._closed:
                return
            if (not enrollment["finishing"] and data_bytes and not len(data_bytes) % 2
                    and len(data_bytes) <= self.MAX_FRAME_BYTES):
                remaining = self.ENROLL_MAX_BYTES - len(enrollment["pcm"])
                enrollment["pcm"].extend(data_bytes[:remaining])
            return
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
            if self.is_sleeping:
                await self.send_state(NexoState.LISTENING, expression="sleeping")
            else:
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
            if self._silence_bytes >= self.silence_bytes or len(self.audio_buffer) >= self.MAX_AUDIO_BYTES:
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
        except Exception as e:
            logger.error("Voice pipeline failed for %s: %s", self.session_id, e, exc_info=True)
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

    async def _speak_canned_reply(self, reply: str, expression: str):
        """Send transcription, publish event, and stream canned reply TTS audio."""
        await self.send_json_message(
            TranscriptionMessage(role="assistant", text=reply, is_final=True)
        )
        if self.hub:
            self.hub.publish_event({
                "type": "transcription",
                "role": "assistant",
                "text": reply,
                "expression": expression,
                "source": "nexo_hermes",
            })
        await self.send_state(NexoState.SPEAKING, expression=expression)
        await self.send_json_message(TTSStart())
        self._tts_started = True

        async for chunk in self.tts.synthesize_stream(reply):
            await asyncio.wait_for(self.websocket.send_bytes(chunk), self.SEND_TIMEOUT)
            self._playback_until = max(time.monotonic(), self._playback_until) + parse_audio_duration(chunk)
            amp = self.vad.get_normalized_amplitude(chunk)
            await self.send_json_message(
                VisualTelemetry(
                    amplitude=max(0.2, amp),
                    frequency_bands=[amp * 0.9, amp * 1.1, amp * 0.7],
                )
            )

    async def _run_pipeline(self, captured_pcm):
        """Process STT -> local mascot command or Hermes -> TTS."""
        # Step 1: Thinking / Transcribing
        if self.is_sleeping:
            await self.send_state(NexoState.THINKING, expression="sleeping")
        else:
            await self.send_state(NexoState.THINKING, expression="bingung")
        transcription = (await self.stt.transcribe(captured_pcm)).strip()
        if not transcription:
            logger.info("No speech transcribed for %s", self.session_id)
            if self.is_sleeping:
                await self.send_state(NexoState.IDLE, expression="sleeping")
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

        if is_voice_enroll_command(transcription) or is_voice_unenroll_command(transcription):
            action = "enroll" if is_voice_enroll_command(transcription) else "manage"
            await self._voiceprint_reply(action, message="Konfirmasi melalui sentuhan di layar.")
            await self._speak_canned_reply(
                "Menu sidik suara dibuka. Konfirmasi melalui sentuhan di layar.",
                MascotExpression.SENANG.value,
            )
            return

        # Step 3: Speaker Verification (if enrolled)
        if self.speaker_verifier is not None and self.speaker_verifier.is_enrolled("owner"):
            is_match, similarity = self.speaker_verifier.verify(captured_pcm, speaker_name="owner")
            if not is_match:
                logger.warning(
                    "Speaker verification rejected speech for session %s (similarity: %.3f < %.3f)",
                    self.session_id, similarity, self.speaker_verifier.threshold
                )
                if self.is_sleeping:
                    await self.send_state(NexoState.IDLE, expression="sleeping")
                    return
                await self._speak_canned_reply(
                    "Maaf, suara tidak dikenali sebagai pemilik.",
                    MascotExpression.BINGUNG.value
                )
                return

        # Step 4: Sleep & Wake Mode Handling
        if is_wake_command(transcription):
            self.is_sleeping = False
            await self._speak_canned_reply(
                "Halo, saya sudah bangun! Ada yang bisa dibantu?",
                MascotExpression.SENANG.value
            )
            return

        if self.is_sleeping:
            # Standby Hening: Ignore other chatter while asleep
            logger.info("Session %s is sleeping; ignoring non-wake input: %s", self.session_id, transcription)
            await self.send_state(NexoState.IDLE, expression="sleeping")
            return

        if is_sleep_command(transcription):
            self.is_sleeping = True
            await self._speak_canned_reply(
                "Baik, saya tidur dulu...",
                MascotExpression.SLEEPING.value
            )
            return

        # Step 3: Only a whole supported mascot command bypasses Hermes.
        command_expression = parse_expression_command(transcription)
        if command_expression is not None:
            await self._speak_canned_reply(
                "Baik, ekspresi wajah sudah diubah.",
                command_expression.value
            )
            return

        # Step 3: Speaking / Streaming TTS from Hermes
        if hasattr(self.hermes, "query_stream"):
            collected_sentences = []
            speech_expr = None
            sentence_expressions = deque()

            async def clean_sentences():
                async with aclosing(self.hermes.query_stream(transcription)) as source:
                    async for sentence in source:
                        tag_expr, clean_sentence = parse_and_strip_emotional_tags(sentence.strip())
                        if clean_sentence:
                            sentence_expressions.append(tag_expr)
                            yield clean_sentence

            async with aclosing(prefetch_tts(self.tts, clean_sentences())) as audio:
                async for clean_sentence, chunk in audio:
                    if chunk is None:
                        tag_expr = sentence_expressions.popleft()
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
                        continue

                    duration = parse_audio_duration(chunk)
                    delay = self._playback_until + duration - self.MAX_PLAYBACK_AHEAD - time.monotonic()
                    if delay > 0:
                        await asyncio.sleep(delay)
                    await asyncio.wait_for(self.websocket.send_bytes(chunk), self.SEND_TIMEOUT)
                    self._playback_until = max(time.monotonic(), self._playback_until) + duration
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
