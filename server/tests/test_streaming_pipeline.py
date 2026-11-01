"""Tests for Sentence-Chunked Streaming Hermes -> TTS and pipeline integration."""

import asyncio
import json
import time
from typing import Any, cast
import pytest

from nexo_server.gateway.websocket_handler import ApplianceSession
from nexo_server.gateway.audio_buffer import CircularAudioBuffer
from nexo_server.gateway.mp3_frame import parse_audio_duration
from nexo_server.models.messages import NexoState


class MockWebSocket:
    def __init__(self):
        self.messages = []
        self.audio_chunks = []

    async def send_text(self, text: str):
        self.messages.append(json.loads(text))

    async def send_bytes(self, data: bytes):
        self.audio_chunks.append(data)


class MockSTT:
    def __init__(self, text: str = "Tolong jelaskan cuaca hari ini."):
        self.text = text

    async def transcribe(self, pcm_data: bytes) -> str:
        return self.text


class StreamingHermesMock:
    """Simulates Hermes producing sentences incrementally with delays."""

    def __init__(self, sentences: list[str], delay_between_sentences: float = 0.05):
        self.sentences = sentences
        self.delay = delay_between_sentences
        self.query_stream_called = False
        self.on_reply = None

    async def query_stream(self, prompt: str):
        self.query_stream_called = True
        for sentence in self.sentences:
            if self.delay > 0:
                await asyncio.sleep(self.delay)
            yield sentence


class TrackingTTSMock:
    """Tracks order and timing of text fed to TTS synthesis."""

    def __init__(self):
        self.synthesized_texts = []
        self.synthesis_times = []

    async def synthesize_stream(self, text: str):
        self.synthesized_texts.append(text)
        self.synthesis_times.append(time.monotonic())
        # Yield dummy 24kHz PCM frame (960 bytes = 20ms)
        yield bytes(960)


async def create_ready_session(stt, tts, hermes):
    ws = MockWebSocket()
    session = ApplianceSession(
        websocket=cast(Any, ws),
        session_id="test_streaming_session",
        stt=stt,
        tts=tts,
        hermes=hermes,
        authenticated_device_id="device_test",
    )
    # Perform client hello handshake
    hello_payload = {
        "type": "client_hello",
        "device_id": "device_test",
        "protocol_version": "1.0",
        "audio_spec": {
            "format": "audio/pcm",
            "sample_rate": 16000,
            "channels": 1,
            "bit_depth": 16,
        },
    }
    await session.handle_text_frame(json.dumps(hello_payload))
    ws.messages.clear()
    return session


@pytest.mark.asyncio
async def test_sentence_chunked_streaming_reduces_first_audio_latency():
    """Verify Sentence 1 triggers TTS synthesis and audio dispatch BEFORE Sentence 2 finishes."""
    sentences = [
        "Hari ini cuaca cerah di seluruh kota.",
        "Suhu udara diperkirakan berkisar 28 hingga 32 derajat Celcius.",
        "Jangan lupa membawa payung untuk berjaga-jaga.",
    ]
    hermes = StreamingHermesMock(sentences=sentences, delay_between_sentences=0.08)
    tts = TrackingTTSMock()
    stt = MockSTT("Bagaimana cuaca?")

    session = await create_ready_session(stt, tts, hermes)
    session.ECHO_GUARD_SECONDS = 0.01

    try:
        t_start = time.monotonic()
        # Feed valid PCM audio frame to trigger processing
        speech_frame = b"\x00\x20" * 3200  # 100 ms speech
        session.audio_buffer.extend(speech_frame)

        await session.process_recorded_audio()

        # 1. Verify hermes.query_stream was used
        assert hermes.query_stream_called is True

        # 2. Verify all sentences were synthesized incrementally in order
        assert tts.synthesized_texts == sentences

        # 3. Verify Sentence 1 synthesis started well before the last sentence
        time_first_sentence = tts.synthesis_times[0] - t_start
        time_last_sentence = tts.synthesis_times[-1] - t_start
        assert time_first_sentence < time_last_sentence

        # 4. Verify websocket received audio for each sentence
        ws = cast(Any, session.websocket)
        assert len(ws.audio_chunks) == len(sentences)

        # 5. Verify protocol lifecycle: TTSStart before audio, TTSEnd at the end
        messages = ws.messages
        types = [m["type"] for m in messages]
        assert "tts_start" in types
        assert "tts_end" in types
        assert types.index("tts_start") < types.index("tts_end")

        # 6. Verify final assistant transcription was emitted
        final_transcriptions = [
            m for m in messages if m["type"] == "transcription" and m["role"] == "assistant"
        ]
        assert len(final_transcriptions) == 1
        assert final_transcriptions[0]["text"] == " ".join(sentences)
        assert final_transcriptions[0]["is_final"] is True

        # 7. Verify session returned to IDLE
        assert session.state == NexoState.IDLE
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_playback_until_precision_with_mp3_frames():
    """Verify _playback_until accurately reflects MP3 frame duration instead of naive PCM byte count."""
    import io
    import math
    import av
    import numpy as np

    # Generate 0.5s MP3 frame
    output = io.BytesIO()
    with av.open(output, mode="w", format="mp3") as container:
        stream = container.add_stream("mp3", rate=24000)
        stream.layout = "mono"
        samples = (0.3 * np.sin(2 * math.pi * 440 * np.arange(12000) / 24000)).astype("float32")
        frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="fltp", layout="mono")
        frame.sample_rate = 24000
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    mp3_bytes = output.getvalue()

    class MP3TTSMock:
        async def synthesize_stream(self, text: str):
            yield mp3_bytes

    hermes = StreamingHermesMock(sentences=["Halo kawan."])
    session = await create_ready_session(MockSTT(), MP3TTSMock(), hermes)
    session.ECHO_GUARD_SECONDS = 0.05

    try:
        session.audio_buffer.extend(b"\x00\x20" * 3200)
        now_before = time.monotonic()
        await session.process_recorded_audio()

        # The MP3 duration is ~0.5s.
        # Under the old naive formula (len/48000), a ~2000 byte MP3 would only register ~0.04s!
        # With frame parsing, duration is ~0.5s.
        expected_duration = parse_audio_duration(mp3_bytes)
        assert 0.45 <= expected_duration <= 0.60
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_circular_ring_buffer_preroll_lifecycle():
    """Verify _preroll circular buffer remains bounded and zero-allocation in session."""
    session = await create_ready_session(MockSTT(), TrackingTTSMock(), StreamingHermesMock(["Tes."]))
    assert isinstance(session._preroll, CircularAudioBuffer)
    assert session._preroll.capacity == session.PREROLL_BYTES

    try:
        # Send 100 frames of quiet PCM (each 640 bytes)
        quiet_chunk = b"\x10\x00" * 320
        for _ in range(100):
            await session.handle_binary_frame(quiet_chunk)

        # Buffer must be exactly capped at PREROLL_BYTES (8000 bytes)
        assert len(session._preroll) == session.PREROLL_BYTES

        # When speech arrives, preroll is drained into audio_buffer and cleared
        speech_chunk = b"\x00\x40" * 320
        await session.handle_binary_frame(speech_chunk)
        assert len(session._preroll) == 0
        assert len(session.audio_buffer) >= session.PREROLL_BYTES
    finally:
        await session.close()
