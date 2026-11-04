"""Regression tests at the session boundary; no network/model dependency."""
import asyncio
import json

import pytest

from nexo_server.gateway.websocket_handler import ApplianceSession


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


async def session():
    stt = STT()
    s = ApplianceSession(Socket(), "test", stt, TTS(), Hermes(), authenticated_device_id="tablet_01")
    await s.handle_text_frame(json.dumps({"type": "client_hello", "device_id": "tablet_01",
        "protocol_version": "1.0", "audio_spec": {"format": "audio/pcm", "sample_rate": 16000,
        "channels": 1, "bit_depth": 16}}))
    s.websocket.messages.clear()
    return s


@pytest.mark.asyncio
async def test_continuous_pcm_endpoints_without_voice_end():
    s = await session()
    for _ in range(15):
        await s.handle_binary_frame(SPEECH)
    for _ in range(80):
        await s.handle_binary_frame(SILENCE)
    try:
        await asyncio.wait_for(s.stt.started.wait(), 0.2)
        assert len(s.stt.received) == 1
        assert s.stt.received[0].startswith(SPEECH)
        assert len(s.stt.received[0]) <= 32000 * 3
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
    s = await session()
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
    s = await session()
    s.AUDIO_GAP_TIMEOUT = 0.02
    try:
        await s.handle_binary_frame(SPEECH * 10)
        await asyncio.wait_for(s.stt.started.wait(), 0.2)
        assert not s.is_recording
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_pipeline_timeout_recovers_and_disconnect_cancels():
    s = await session()
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
    s = await session()
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
    s = await session()
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
    s = await session()
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
    s = await session()
    s.stt.release.set()
    s.tts = BrokenTTS()
    try:
        await s.handle_binary_frame(SPEECH * 10)
        await s.handle_text_frame('{"type":"voice_end"}')
        await asyncio.wait_for(s._processing_task, 1.5)
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
    s = await session()
    s.stt = EmptySTT()
    try:
        await s.handle_binary_frame(SPEECH * 10)
        await s.handle_text_frame('{"type":"voice_end"}')
        await asyncio.wait_for(s._processing_task, 1.5)
        assert not s.websocket.audio
        assert not any(m["type"] == "transcription" for m in s.websocket.messages)
        assert s.websocket.messages[-1]["state"] == "IDLE"
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_cooldown_covers_queued_playback_and_echo():
    s = await session()
    s.stt.release.set()
    try:
        await s.handle_binary_frame(SPEECH * 10)
        await s.handle_text_frame('{"type":"voice_end"}')
        # While hardware audio/echo can still be pending, capture remains gated.
        while not s.websocket.audio:
            await asyncio.sleep(0)
        await s.handle_binary_frame(SPEECH)
        assert not s.audio_buffer
        assert not s.is_recording
        await asyncio.wait_for(s._processing_task, 1.5)
        assert s.websocket.audio == [bytes(960)]
        assert sum(m["type"] == "tts_end" for m in s.websocket.messages) == 1
        # IDLE is now advertised only after that guard has elapsed.
        await s.handle_binary_frame(SPEECH)
        assert s.is_recording
    finally:
        await s.close()


class FixedSTT:
    def __init__(self, text):
        self.text = text

    async def transcribe(self, pcm):
        return self.text


class TrackingHermes:
    def __init__(self, reply="Halo, tes berhasil. Haha!"):
        self.reply = reply
        self.calls = []

    async def query(self, text):
        self.calls.append(text)
        return self.reply


class TrackingTTS:
    def __init__(self):
        self.texts = []

    async def synthesize_stream(self, text):
        self.texts.append(text)
        yield bytes(960)


@pytest.mark.asyncio
@pytest.mark.parametrize(("command", "expression"), [
    ("Nexo tersenyum", "senang"),
    ("Nexo tersenyum", "senang"),
    ("tunjukkan wajah bingung", "bingung"),
    ("coba tertawa", "tertawa"),
    ("ekspresi netral", "netral"),
    ("kembali normal", "netral"),
    ("jangan sedih", "senang"),
    ("ekspresi sedih", "sedih"),
])
async def test_pure_mascot_command_bypasses_agent_and_preserves_ack(command, expression):
    s = await session()
    s.stt = FixedSTT(command)
    s.hermes = TrackingHermes()
    s.tts = TrackingTTS()
    try:
        s.audio_buffer.extend(SPEECH * 10)
        await s.process_recorded_audio()
        assert s.hermes.calls == []
        assistant = [m["text"] for m in s.websocket.messages
                     if m["type"] == "transcription" and m["role"] == "assistant"]
        assert assistant == ["Baik, ekspresi wajah sudah diubah."]
        assert s.tts.texts == assistant
        assert s.websocket.audio == [bytes(960)]
        speaking = [m for m in s.websocket.messages if m.get("state") == "SPEAKING"]
        assert [m["expression"] for m in speaking] == [expression]
        assert sum(m["type"] == "tts_end" for m in s.websocket.messages) == 1
        assert s.websocket.messages[-1]["state"] == "IDLE"
    finally:
        await s.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("text", "reply", "expression"), [
    ("Nexo tersenyum lalu buka email", "Email berhasil dibuka.", "sangat_senang"),
    ("Nexo tersenyum lalu buka email", "Email berhasil dibuka.", "sangat_senang"),
    ('Jelaskan "coba tertawa"', "Pengalaman manusia beragam.", "netral"),
    ("jangan tertawa", "Baik.", "netral"),
    ("ekspresi lapar", "Saya belum yakin.", "bingung"),
    ("kenapa Nexo tersenyum?", "Halo!", "senang"),
    ("kenapa Nexo tersenyum?", "Halo!", "senang"),
    ("Aku sedih hari ini", "Haha, selamat pagi!", "sedih"),
    ("Ceritakan lelucon", "Mengapa ayam menyeberang jalan?", "tertawa"),
    ("Jelaskan pengalaman", "Pengalaman adalah pembelajaran.", "netral"),
])
async def test_normal_tasks_use_agent_and_context_without_rewriting_reply(text, reply, expression):
    s = await session()
    s.stt = FixedSTT(text)
    s.hermes = TrackingHermes(reply)
    s.tts = TrackingTTS()
    try:
        s.audio_buffer.extend(SPEECH * 10)
        await s.process_recorded_audio()
        assert s.hermes.calls == [text]
        assert s.tts.texts == [reply]
        assert [m["text"] for m in s.websocket.messages
                if m["type"] == "transcription" and m["role"] == "assistant"] == [reply]
        assert [m["expression"] for m in s.websocket.messages
                if m.get("state") == "SPEAKING"] == [expression]
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_empty_transcription_calls_neither_agent_nor_tts():
    s = await session()
    s.stt = FixedSTT(" \n ")
    s.hermes = TrackingHermes()
    s.tts = TrackingTTS()
    try:
        s.audio_buffer.extend(SPEECH * 10)
        await s.process_recorded_audio()
        assert not s.hermes.calls
        assert not s.tts.texts
        assert not any(m["type"] == "tts_start" for m in s.websocket.messages)
    finally:
        await s.close()


def test_voice_session_dual_compatibility():
    """Verify legacy nexo_server websocket handler alias matches nexo_server."""
    from nexo_server.gateway.websocket_handler import ApplianceSession as LegacySession
    assert LegacySession is not None


@pytest.mark.asyncio
async def test_sleep_and_wake_lifecycle():
    s = await session()
    s.tts = TrackingTTS()
    s.hermes = TrackingHermes("Saya siap membantu.")

    try:
        assert s.is_sleeping is False

        # 1. User says "Nexo, tidur dulu"
        s.stt = FixedSTT("Nexo, tidur dulu")
        s.audio_buffer.extend(SPEECH * 10)
        await s.process_recorded_audio()

        assert s.is_sleeping is True
        assert s.hermes.calls == []
        assert "Baik, saya tidur dulu..." in s.tts.texts
        last_state = [m for m in s.websocket.messages if "state" in m][-1]
        assert last_state["state"] == "IDLE"
        assert last_state["expression"] == "sleeping"

        # 2. In sleep mode: non-wake chatter e.g. "hari ini makan apa"
        s.websocket.messages.clear()
        s.tts.texts.clear()
        s.stt = FixedSTT("hari ini makan apa")
        s.audio_buffer.extend(SPEECH * 10)
        await s.process_recorded_audio()

        assert s.is_sleeping is True
        assert s.hermes.calls == []
        assert s.tts.texts == []
        last_state = [m for m in s.websocket.messages if "state" in m][-1]
        assert last_state["state"] == "IDLE"
        assert last_state["expression"] == "sleeping"

        # 3. User says "Nexo, bangun"
        s.websocket.messages.clear()
        s.tts.texts.clear()
        s.stt = FixedSTT("Nexo, bangun")
        s.audio_buffer.extend(SPEECH * 10)
        await s.process_recorded_audio()

        assert s.is_sleeping is False
        assert s.hermes.calls == []
        assert "Halo, saya sudah bangun! Ada yang bisa dibantu?" in s.tts.texts
        speaking = [m for m in s.websocket.messages if m.get("state") == "SPEAKING"]
        assert [m["expression"] for m in speaking] == ["senang"]
        last_state = [m for m in s.websocket.messages if "state" in m][-1]
        assert last_state["state"] == "IDLE"
        assert last_state["expression"] == "netral"

        # 4. Once awake, normal conversation queries work again
        s.websocket.messages.clear()
        s.tts.texts.clear()
        s.stt = FixedSTT("buka email")
        s.audio_buffer.extend(SPEECH * 10)
        await s.process_recorded_audio()

        assert s.hermes.calls == ["buka email"]
        assert s.tts.texts == ["Saya siap membantu."]
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_faster_whisper_stt_percentile_gain_ignores_mic_clicks():
    """Verify quiet speech with a transient mic click is still boosted properly in FasterWhisperSTT."""
    from types import SimpleNamespace
    import numpy as np
    from nexo_server.pipeline.stt import FasterWhisperSTT as NexoSTT
    from jarvis_server.pipeline.stt import FasterWhisperSTT as JarvisSTT

    # 1 second of quiet 400 Hz speech (amplitude ~0.03 = ~983 int16) + 2 samples of loud mic click (0.85)
    t = np.linspace(0, 1.0, 16000, dtype=np.float32)
    wave = 0.03 * np.sin(2 * np.pi * 400 * t)
    wave[0] = 0.85
    wave[1] = -0.85
    pcm_bytes = (wave * 32767).astype(np.int16).tobytes()

    for cls in (NexoSTT, JarvisSTT):
        stt = cls(model_size="base")
        captured_arrays = []
        captured_kwargs = []

        class FakeModel:
            def transcribe(self, audio_array, **kwargs):
                captured_arrays.append(audio_array)
                captured_kwargs.append(kwargs)
                return [SimpleNamespace(text=" Halo Nexo ", no_speech_prob=0.60)], None

        stt._model = FakeModel()
        text = await stt.transcribe(pcm_bytes)
        assert text == "Halo Nexo"
        assert len(captured_arrays) == 1
        # Verify the quiet speech body (excluding the 2 click samples) was boosted significantly (> 0.20)
        speech_body_peak = float(np.percentile(np.abs(captured_arrays[0][10:]), 99.0))
        assert speech_body_peak > 0.20
        assert captured_kwargs[0]["no_speech_threshold"] == 0.65

