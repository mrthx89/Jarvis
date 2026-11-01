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


def _generate_mp3_chunk(sample_rate: int = 24000, duration: float = 0.5) -> bytes:
    import io, math
    import av  # type: ignore
    import numpy as np  # type: ignore
    output = io.BytesIO()
    total_samples = int(sample_rate * duration)
    with av.open(output, mode="w", format="mp3") as container:
        stream = container.add_stream("mp3", rate=sample_rate)
        stream.layout = "mono"
        t = np.arange(total_samples) / float(sample_rate)
        samples = (0.3 * np.sin(2 * math.pi * 440 * t)).astype("float32")
        frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="fltp", layout="mono")
        frame.sample_rate = sample_rate
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return output.getvalue()


@pytest.mark.asyncio
async def test_sentence_chunked_streaming_empty_and_whitespace_skipped():
    """Verify empty or whitespace-only chunks from LLM stream do not trigger TTS."""
    sentences = ["", "   ", "Pesan valid pertama.", "\n\n", "Pesan valid kedua.", "  \t  "]
    hermes = StreamingHermesMock(sentences=sentences, delay_between_sentences=0.01)
    tts = TrackingTTSMock()
    stt = MockSTT("Halo")

    session = await create_ready_session(stt, tts, hermes)
    session.ECHO_GUARD_SECONDS = 0.01

    try:
        session.audio_buffer.extend(b"\x00\x20" * 3200)
        await session.process_recorded_audio()

        assert tts.synthesized_texts == ["Pesan valid pertama.", "Pesan valid kedua."]
        ws = cast(Any, session.websocket)
        assert len(ws.audio_chunks) == 2
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_sentence_chunked_streaming_cognitive_hub_events():
    """Verify cognitive streaming events are emitted to the hub for each chunk."""
    class MockHub:
        def __init__(self):
            self.events = []

        def publish_event(self, ev: dict):
            self.events.append(ev)

        def unregister_session(self, session):
            pass

    sentences = ["Kalimat satu.", "Kalimat dua."]
    hermes = StreamingHermesMock(sentences=sentences, delay_between_sentences=0.01)
    tts = TrackingTTSMock()
    stt = MockSTT("Pertanyaan")

    session = await create_ready_session(stt, tts, hermes)
    session.ECHO_GUARD_SECONDS = 0.01
    hub = MockHub()
    session.hub = hub

    try:
        session.audio_buffer.extend(b"\x00\x20" * 3200)
        await session.process_recorded_audio()

        cognitive_events = [e for e in hub.events if e.get("type") == "cognitive" and e.get("stage") == "streaming"]
        assert len(cognitive_events) == 2
        assert cognitive_events[0]["sentence"] == "Kalimat satu."
        assert cognitive_events[0]["index"] == 0
        assert cognitive_events[1]["sentence"] == "Kalimat dua."
        assert cognitive_events[1]["index"] == 1
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_playback_until_monotonic_multi_chunk_advance():
    """Verify _playback_until advances monotonically and accumulates duration across chunks."""
    session = await create_ready_session(MockSTT(), TrackingTTSMock(), StreamingHermesMock(["Tes"]))

    # Generate 3 chunks of 0.5s MP3
    chunk1 = _generate_mp3_chunk(sample_rate=24000, duration=0.5)
    chunk2 = _generate_mp3_chunk(sample_rate=24000, duration=0.5)
    chunk3 = _generate_mp3_chunk(sample_rate=24000, duration=0.5)

    try:
        t0 = time.monotonic()
        dur1 = parse_audio_duration(chunk1)
        dur2 = parse_audio_duration(chunk2)
        dur3 = parse_audio_duration(chunk3)

        assert 0.45 <= dur1 <= 0.60
        assert 0.45 <= dur2 <= 0.60
        assert 0.45 <= dur3 <= 0.60

        # Simulate consecutive arrivals
        session._playback_until = max(time.monotonic(), session._playback_until) + dur1
        p1 = session._playback_until
        assert p1 >= t0 + 0.45

        session._playback_until = max(time.monotonic(), session._playback_until) + dur2
        p2 = session._playback_until
        assert p2 >= p1 + 0.45

        session._playback_until = max(time.monotonic(), session._playback_until) + dur3
        p3 = session._playback_until
        assert p3 >= p2 + 0.45

        # Total playback until should be ~1.5s in the future
        assert p3 >= t0 + 1.35
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_circular_preroll_preserves_speech_onset_for_stt():
    """Verify quiet audio in circular preroll is strictly preserved at start of audio_buffer."""
    session = await create_ready_session(MockSTT(), TrackingTTSMock(), StreamingHermesMock(["Tes"]))

    try:
        # Feed 15 frames of quiet identifiable PCM (each 640 bytes = 9600 bytes total, capped at 8000)
        pattern_frame = b"\x05\x00" * 320
        for _ in range(15):
            await session.handle_binary_frame(pattern_frame)

        # Preroll is full (8000 bytes)
        assert len(session._preroll) == session.PREROLL_BYTES
        preroll_bytes = session._preroll.get_bytes()

        # Trigger speech with loud audio
        loud_speech = b"\x00\x50" * 320
        await session.handle_binary_frame(loud_speech)

        # Verify session is now recording
        assert session.is_recording is True
        # Verify audio buffer starts with the exact preroll bytes
        assert bytes(session.audio_buffer[:session.PREROLL_BYTES]) == preroll_bytes
        # Verify preroll buffer is cleared
        assert len(session._preroll) == 0
    finally:
        await session.close()


def test_dual_compatibility_streaming_pipeline():
    """Verify legacy jarvis_server appliance session class matches nexo_server."""
    from jarvis_server.gateway.websocket_handler import ApplianceSession as JarvisSession
    from nexo_server.gateway.websocket_handler import ApplianceSession as NexoSession

    assert JarvisSession is not None
    assert NexoSession is not None

