"""Gateway behavior with deterministic offline providers and websocket sinks."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from nexo_server.gateway import websocket_handler as gateway
from nexo_server.gateway.websocket_handler import ApplianceSession
from nexo_server.pipeline.stt import BaseSTT


class Socket:
    def __init__(self):
        self.events = []
        self.audio = []

    async def send_text(self, text):
        self.events.append(json.loads(text))

    async def send_bytes(self, pcm):
        self.audio.append(pcm)
        self.events.append({"type": "audio", "pcm": pcm})


class STT(BaseSTT):
    async def transcribe(self, pcm_audio, language="id"):
        return "Apa kabar hari ini?"


def session_for(tts, sentences, socket=None):
    def query_stream(text):
        return sentences()

    return ApplianceSession(
        socket or Socket(), "continuity", STT(), tts,
        SimpleNamespace(query_stream=query_stream), authenticated_device_id="tablet",
    )


@pytest.mark.asyncio
async def test_next_sentence_synthesizes_while_previous_socket_send_is_blocked():
    second_started = asyncio.Event()

    class TTS:
        async def synthesize_stream(self, text):
            if text == "Kedua.":
                second_started.set()
            yield b"\x01\x00" * 24

    class SlowSocket(Socket):
        async def send_bytes(self, pcm):
            await asyncio.wait_for(second_started.wait(), 0.2)
            await super().send_bytes(pcm)

    async def sentences():
        yield "Pertama."
        yield "Kedua."

    session = session_for(TTS(), sentences, SlowSocket())
    await session._run_pipeline(bytes(3200))
    assert len(session.websocket.audio) == 2


@pytest.mark.asyncio
async def test_streaming_pcm_is_paced_with_at_most_one_second_client_backlog(monkeypatch):
    now = 100.0
    backlogs = []
    sleeps = []
    real_sleep = asyncio.sleep

    async def advance(delay):
        nonlocal now
        sleeps.append(delay)
        now += delay
        await real_sleep(0)

    class TTS:
        async def synthesize_stream(self, text):
            yield b"\x01\x00" * 72000

    class MeasuredSocket(Socket):
        async def send_bytes(self, pcm):
            ahead = max(0.0, session._playback_until - now) + len(pcm) / 48000
            backlogs.append(ahead)
            await super().send_bytes(pcm)

    async def sentences():
        yield "Panjang."

    monkeypatch.setattr(gateway, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(gateway.asyncio, "sleep", advance)
    session = session_for(TTS(), sentences, MeasuredSocket())
    await session._run_pipeline(bytes(3200))
    assert max(backlogs) <= 1.0 + 1e-9, "Gateway overfills Android playback queue"
    assert sum(sleeps) == pytest.approx(2.0)
    assert b"".join(session.websocket.audio) == b"\x01\x00" * 72000
    assert all(len(pcm) <= 4800 for pcm in session.websocket.audio)


@pytest.mark.asyncio
async def test_emotions_and_cognitive_events_follow_audio_boundaries_without_tags_spoken():
    spoken = []
    payloads = [b"\x01\x00" * 24, b"\x02\x00" * 24, b"\x03\x00" * 24]

    class TTS:
        async def synthesize_stream(self, text):
            spoken.append(text)
            yield payloads[len(spoken) - 1]

    async def sentences():
        yield "   "
        yield "[expr:senang] Halo."
        yield "[expr:sedih] Halo."
        yield "Lanjut."

    socket = Socket()
    session = session_for(TTS(), sentences, socket)
    session.hub = SimpleNamespace(publish_event=socket.events.append)
    session.ECHO_GUARD_SECONDS = 0
    session.audio_buffer.extend(bytes(3200))
    await session.process_recorded_audio()

    assert spoken == ["Halo.", "Halo.", "Lanjut."]
    assert socket.audio == payloads
    events = socket.events
    cognitive = [e for e in events if e["type"] == "cognitive"]
    assert [(e["index"], e["sentence"], e["expression"]) for e in cognitive] == [
        (0, "Halo.", "senang"), (1, "Halo.", "sedih"), (2, "Lanjut.", "sedih")
    ]
    audio_indices = [i for i, e in enumerate(events) if e["type"] == "audio"]
    for i, event in enumerate(cognitive):
        assert events.index(event) < audio_indices[i]
        if i:
            assert audio_indices[i - 1] < events.index(event)
    speaking = [e for e in events if e["type"] == "state_change" and e["state"] == "SPEAKING"]
    assert [e["expression"] for e in speaking] == ["senang", "senang", "sedih", "sedih"]
    assert events.index(speaking[0]) < next(i for i, e in enumerate(events) if e["type"] == "tts_start")
    assert sum(e["type"] == "tts_start" for e in events) == 1
    assert sum(e["type"] == "tts_end" for e in events) == 1
    final = next(e for e in events if e["type"] == "transcription" and e.get("role") == "assistant")
    assert final["text"] == "Halo. Halo. Lanjut."
    assert audio_indices[-1] < events.index(final) < next(i for i, e in enumerate(events) if e["type"] == "tts_end")
    assert events[-1]["state"] == "IDLE"


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_at", ["provider", "source", "pacing"])
async def test_cancellation_closes_provider_and_sentence_source(monkeypatch, blocked_at):
    closed = []
    blocked = asyncio.Event()
    sent = asyncio.Event()

    class TTS:
        async def synthesize_stream(self, text):
            try:
                yield b"\x01\x00" * (72000 if blocked_at == "pacing" else 24)
                if blocked_at == "provider":
                    blocked.set()
                    await asyncio.Event().wait()
            finally:
                closed.append("provider")

    async def sentences():
        try:
            yield "Pertama."
            if blocked_at == "source":
                blocked.set()
                await asyncio.Event().wait()
        finally:
            closed.append("source")

    class BlockingSocket(Socket):
        async def send_bytes(self, pcm):
            sent.set()
            if blocked_at != "pacing":
                await asyncio.Event().wait()
            await super().send_bytes(pcm)

    if blocked_at == "pacing":
        async def block_sleep(delay):
            blocked.set()
            await asyncio.Event().wait()
        monkeypatch.setattr(gateway.asyncio, "sleep", block_sleep)

    session = session_for(TTS(), sentences, BlockingSocket())
    task = asyncio.create_task(session._run_pipeline(bytes(3200)))
    try:
        await asyncio.wait_for(blocked.wait(), 0.5)
        await asyncio.wait_for(sent.wait(), 0.5)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert closed == ["provider", "source"]


@pytest.mark.asyncio
async def test_socket_failure_closes_prefetched_provider_and_sentence_source():
    closed = []

    class TTS:
        async def synthesize_stream(self, text):
            try:
                yield b"\x01\x00" * 24
                await asyncio.Event().wait()
            finally:
                closed.append("provider")

    async def sentences():
        try:
            yield "Pertama."
        finally:
            closed.append("source")

    class BrokenSocket(Socket):
        async def send_bytes(self, pcm):
            raise ConnectionError("offline socket failure")

    session = session_for(TTS(), sentences, BrokenSocket())
    with pytest.raises(ConnectionError, match="offline socket failure"):
        await session._run_pipeline(bytes(3200))
    assert closed == ["provider", "source"]
