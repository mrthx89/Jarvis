"""Regression tests at the session boundary; no network/model dependency."""
import asyncio
import json

import pytest

from jarvis_server.gateway.websocket_handler import ApplianceSession


class Socket:
    def __init__(self):
        self.messages = []
        self.audio = []

    async def send_text(self, data):
        self.messages.append(json.loads(data))

    async def send_bytes(self, data):
        self.audio.append(data)


class STT:
    def __init__(self):
        self.received = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def transcribe(self, pcm):
        self.received.append(pcm)
        self.started.set()
        await self.release.wait()
        return "Halo halo"


class Hermes:
    async def query(self, text):
        return "Halo, saya siap."


class TTS:
    async def synthesize_stream(self, text):
        yield bytes(960)


SPEECH = b"\x00\x20" * 320  # 20 ms @ 16 kHz
SILENCE = bytes(640)


def session():
    stt = STT()
    return ApplianceSession(Socket(), "test", stt, TTS(), Hermes())


@pytest.mark.asyncio
async def test_continuous_pcm_endpoints_without_voice_end():
    s = session()
    for _ in range(15):
        await s.handle_binary_frame(SPEECH)
    for _ in range(50):
        await s.handle_binary_frame(SILENCE)
    try:
        await asyncio.wait_for(s.stt.started.wait(), 0.2)
        assert len(s.stt.received) == 1
        assert s.stt.received[0].startswith(SPEECH)
        assert len(s.stt.received[0]) <= 32000 * 2
        assert not s.audio_buffer
        # While STT is blocked, heartbeat still works and echo is discarded.
        await s.handle_text_frame('{"type":"ping","timestamp":123}')
        await s.handle_binary_frame(SPEECH)
        assert s.websocket.messages[-1] == {"type": "pong", "timestamp": 123}
        assert not s.audio_buffer
    finally:
        if hasattr(s, "close"):
            await s.close()


@pytest.mark.asyncio
async def test_continuous_noise_is_bounded():
    s = session()
    try:
        for _ in range(1600):  # 32 seconds, no silence/voice_end
            await s.handle_binary_frame(SPEECH)
        await asyncio.wait_for(s.stt.started.wait(), 0.2)
        assert len(s.stt.received[0]) <= 30 * 32000
        assert not s.audio_buffer
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_audio_gap_endpoints_when_client_stops_sending():
    s = session()
    s.AUDIO_GAP_TIMEOUT = 0.02
    try:
        await s.handle_binary_frame(SPEECH * 10)
        await asyncio.wait_for(s.stt.started.wait(), 0.2)
        assert not s.is_recording
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_pipeline_timeout_recovers_and_disconnect_cancels():
    s = session()
    s.PROCESSING_TIMEOUT = 0.02
    try:
        await s.handle_binary_frame(SPEECH * 10)
        await s.handle_text_frame('{"type":"voice_end"}')
        await asyncio.sleep(0.05)
        states = [m["state"] for m in s.websocket.messages if m["type"] == "state_change"]
        assert "ERROR" in states
        assert states[-1] == "IDLE"
        assert s._processing_task.done()
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_disconnected_session_cancels_pending_processing():
    s = session()
    await s.handle_binary_frame(SPEECH * 10)
    await s.handle_text_frame('{"type":"voice_end"}')
    await asyncio.wait_for(s.stt.started.wait(), 0.2)
    await s.close()
    assert s._processing_task.done()
    assert not s.audio_buffer
    await s.handle_binary_frame(SPEECH)
    assert not s.is_recording


@pytest.mark.asyncio
async def test_invalid_audio_frames_are_dropped_before_vad():
    s = session()
    try:
        for invalid in (b"", b"\xff", SPEECH * 101):
            await s.handle_binary_frame(invalid)
        assert not s.is_recording
        assert not s.audio_buffer
        assert not s.websocket.messages
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_preroll_preserves_quiet_onset_and_stays_bounded():
    s = session()
    quiet = b"\x40\x00" * 320
    try:
        for _ in range(40):
            await s.handle_binary_frame(quiet)
        assert len(s._preroll) <= 32000 * 0.3
        await s.handle_binary_frame(SPEECH * 10)
        await s.handle_text_frame('{"type":"voice_end"}')
        await asyncio.wait_for(s.stt.started.wait(), 0.2)
        assert s.stt.received[0].startswith(quiet)
        assert 32000 * 0.2 <= s.stt.received[0].find(SPEECH) <= 32000 * 0.3
        assert not s._preroll
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_failed_tts_still_ends_stream_and_recovers():
    class BrokenTTS:
        async def synthesize_stream(self, text):
            yield bytes(960)
            raise RuntimeError("provider failed mid-stream")
    s = session()
    s.stt.release.set()
    s.tts = BrokenTTS()
    try:
        await s.handle_binary_frame(SPEECH * 10)
        await s.handle_text_frame('{"type":"voice_end"}')
        await asyncio.wait_for(s._processing_task, 0.5)
        assert sum(m["type"] == "tts_start" for m in s.websocket.messages) == 1
        assert sum(m["type"] == "tts_end" for m in s.websocket.messages) == 1
        assert any(m.get("state") == "ERROR" for m in s.websocket.messages)
        assert s.websocket.messages[-1]["state"] == "IDLE"
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_empty_transcription_skips_hermes_and_speech():
    class EmptySTT:
        async def transcribe(self, pcm):
            return "  "
    s = session()
    s.stt = EmptySTT()
    try:
        await s.handle_binary_frame(SPEECH * 10)
        await s.handle_text_frame('{"type":"voice_end"}')
        await asyncio.wait_for(s._processing_task, 0.5)
        assert not s.websocket.audio
        assert not any(m["type"] == "transcription" for m in s.websocket.messages)
        assert s.websocket.messages[-1]["state"] == "IDLE"
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_cooldown_covers_queued_playback_and_echo():
    s = session()
    s.stt.release.set()
    try:
        await s.handle_binary_frame(SPEECH * 10)
        await s.handle_text_frame('{"type":"voice_end"}')
        await asyncio.wait_for(s._processing_task, 0.5)
        assert s.websocket.audio == [bytes(960)]
        assert sum(m["type"] == "tts_end" for m in s.websocket.messages) == 1
        await s.handle_text_frame('{"type":"voice_start"}')
        await s.handle_binary_frame(SPEECH)
        assert not s.audio_buffer
        assert not s.is_recording
    finally:
        await s.close()
