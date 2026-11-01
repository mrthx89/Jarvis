"""Voice reception regressions; deterministic PCM, no providers or hardware."""
import asyncio
import json
import struct
import time

import pytest

from nexo_server.gateway.websocket_handler import ApplianceSession


class Socket:
    def __init__(self):
        self.messages = []
        self.session = None
        self.busy_at_idle = []

    async def send_text(self, raw):
        message = json.loads(raw)
        self.messages.append(message)
        if message.get('state') == 'IDLE':
            self.busy_at_idle.append(time.monotonic() < self.session._cooldown_until)

    async def send_bytes(self, data):
        pass


class STT:
    async def transcribe(self, pcm):
        return ''


class Hermes:
    async def query(self, text):
        raise AssertionError('Empty speech must not call Hermes')


class TTS:
    async def synthesize_stream(self, text):
        yield bytes(960)


def pcm(amplitude, samples=1600):
    return struct.pack('<h', amplitude) * samples


def make_session():
    socket = Socket()
    session = ApplianceSession(socket, 'voice-test', STT(), TTS(), Hermes(),
                               authenticated_device_id='tablet')
    socket.session = session
    session.ready = True
    return session


@pytest.mark.asyncio
async def test_empty_stt_resumes_capture_immediately_without_echo_cooldown():
    session = make_session()
    try:
        session.audio_buffer.extend(pcm(4000))
        await session.process_recorded_audio()
        assert session._cooldown_until <= time.monotonic()
        await session.handle_binary_frame(pcm(4000))
        assert session.is_recording, 'First word after empty STT must not be discarded'
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_idle_is_not_advertised_during_server_echo_guard():
    session = make_session()
    try:
        await session.speak_text('Test')
        assert session.websocket.busy_at_idle == [False]
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_direct_speech_does_not_capture_its_own_speaker_echo():
    session = make_session()
    started = asyncio.Event()
    release = asyncio.Event()

    class SlowTTS:
        async def synthesize_stream(self, text):
            started.set()
            await release.wait()
            yield bytes(960)

    session.tts = SlowTTS()
    task = asyncio.create_task(session.speak_text('Test'))
    try:
        await asyncio.wait_for(started.wait(), 0.5)
        await session.handle_binary_frame(pcm(4000))
        assert not session.is_recording
        assert not session.audio_buffer
    finally:
        release.set()
        await task
        await session.close()


@pytest.mark.asyncio
async def test_soft_syllables_continue_an_already_started_utterance():
    session = make_session()
    try:
        await session.handle_binary_frame(pcm(4000))
        # -40 dBFS speech is quieter than the onset threshold, not a 1.5s pause.
        for _ in range(20):
            await session.handle_binary_frame(pcm(328))
        assert session.is_recording
        assert session._processing_task is None
        assert len(session.audio_buffer) == 21 * 3200
        for _ in range(15):
            await session.handle_binary_frame(bytes(3200))
        assert session._processing_task is not None
        await session._processing_task
    finally:
        await session.close()
