"""Pipeline outcomes remain private and owner-gated on the v2 path."""
import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from tests.test_outdoor_gateway import connect, control, uid


class Hub:
    def __init__(self):
        self.events = []

    def publish_event(self, event):
        self.events.append(event)

    def unregister_session(self, session):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("transcript", ["Jelaskan cuaca besok", "tidur dulu"])
async def test_cancel_resistant_stt_cannot_trigger_stale_effects(transcript):
    s, ws = await connect()
    entered, release = asyncio.Event(), asyncio.Event()

    async def transcribe(pcm):
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        return transcript

    async def stream(*args):
        if False:
            yield b""

    s.stt.transcribe = transcribe
    s.hermes.query_stream = Mock(side_effect=stream)
    s.tts.synthesize_stream = Mock(side_effect=stream)
    s.hub = Hub()
    try:
        await control(s, "conversation_resume")
        token = s.outdoor._output_generation.set(s.outdoor.generation)
        task = asyncio.create_task(s._run_pipeline(b"\x01\x02" * 2000))
        s.outdoor._output_generation.reset(token)
        await entered.wait()
        s.hub.events.clear()
        s.outdoor.generation += 1
        task.cancel()
        release.set()
        assert await task == "cancelled"
        s.hermes.query_stream.assert_not_called()
        assert s.hub.events == []
        assert s.is_sleeping is False
        assert not any(f.get("type") == "transcription" for f in ws.frames if isinstance(f, dict))
    finally:
        release.set()
        await s.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["daftarkan suara saya", "hapus sidik suara", "tidur dulu", "bangun", "ubah ekspresi senang"])
async def test_v2_rejects_non_owner_before_local_commands(text):
    s, ws = await connect()
    s.stt.transcribe.return_value = text
    s.speaker_verifier = Mock()
    s.speaker_verifier.verify.return_value = (False, 0.1)
    s.speaker_verifier.threshold = 0.6
    s.speaker_verifier.get_metadata.return_value = {}

    async def audio(*args):
        yield b"\0\0"

    s.tts.synthesize_stream = Mock(side_effect=audio)
    s.hub = Hub()
    try:
        await control(s, "conversation_resume")
        assert await s._run_pipeline(b"\x01\x02" * 8000) == "speaker_rejected"
        s.speaker_verifier.verify.assert_called_once()
        s.hermes.query_stream.assert_not_called()
        assert s.is_sleeping is False
        assert not any(f.get("type") in {"voiceprint_dialog", "tts_start", "transcription"}
                       for f in ws.frames if isinstance(f, dict))
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_insufficient_verification_input_is_not_identity_mismatch():
    s, ws = await connect()
    s.stt.transcribe.return_value = "tidur dulu"
    s.speaker_verifier = Mock()
    s.speaker_verifier.verify.return_value = (False, 0.0)
    try:
        await control(s, "conversation_resume")
        assert await s._run_pipeline(b"\x01\x02" * 2000) == "quality_insufficient"
        s.speaker_verifier.verify.assert_called_once()
        s.speaker_verifier.extract_embedding.assert_not_called()
        assert not s.is_sleeping
    finally:
        await s.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_late_hermes_result_cannot_publish_or_start_tts(streaming):
    from types import SimpleNamespace
    s, ws = await connect()
    s.stt.transcribe.return_value = "Jelaskan cuaca"
    entered, release = asyncio.Event(), asyncio.Event()

    async def query(text):
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        return "Cerah."

    async def stream(text):
        yield await query(text)

    async def audio(text):
        yield b"\0\0"

    s.hermes = SimpleNamespace(**({"query_stream": stream} if streaming else {"query": query}))
    s.tts.synthesize_stream = Mock(side_effect=audio)
    s.hub = Hub()
    try:
        await control(s, "conversation_resume")
        token = s.outdoor._output_generation.set(s.outdoor.generation)
        task = asyncio.create_task(s._run_pipeline(b"\x01\x02" * 8000))
        s.outdoor._output_generation.reset(token)
        await entered.wait()
        s.hub.events.clear()
        s.outdoor.generation += 1
        release.set()
        assert await task == "cancelled"
        s.tts.synthesize_stream.assert_not_called()
        assert s.hub.events == []
    finally:
        release.set()
        await s.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["stt", "hermes_query", "hermes_stream", "tts_query", "tts_stream"])
async def test_v2_provider_errors_return_safe_exact_outcomes(provider, caplog):
    from types import SimpleNamespace
    s, ws = await connect()
    s.stt.transcribe.return_value = "Jelaskan cuaca"

    async def query(text):
        if provider == "hermes_query":
            raise RuntimeError("private transcript token audio")
        return "Cerah."

    async def stream(text):
        if provider == "hermes_stream":
            raise RuntimeError("private transcript token audio")
        yield "Cerah."

    async def audio(text):
        if provider.startswith("tts"):
            raise RuntimeError("private transcript token audio")
        yield b"\0\0"

    s.hermes = SimpleNamespace(**({"query_stream": stream} if provider.endswith("stream") else {"query": query}))
    s.tts.synthesize_stream = audio
    if provider == "stt":
        s.stt.transcribe.side_effect = RuntimeError("private transcript token audio")
    try:
        await control(s, "conversation_resume")
        expected = provider.split("_")[0] + "_failed"
        assert await s._run_outdoor_pipeline(b"\x01\x02" * 8000) == expected
        assert "private transcript token audio" not in caplog.text
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_verification_wait_keeps_event_loop_live_and_fences_late_owner():
    import threading
    s, ws = await connect()
    s.stt.transcribe.return_value = "tidur dulu"
    entered, release = threading.Event(), threading.Event()

    def verify(*args, **kwargs):
        entered.set()
        release.wait(1)
        return True, 0.99

    s.speaker_verifier = Mock()
    s.speaker_verifier.verify.side_effect = verify

    async def audio(text):
        yield b"\0\0"

    s.tts.synthesize_stream = audio
    try:
        await control(s, "conversation_resume")
        token = s.outdoor._output_generation.set(s.outdoor.generation)
        task = asyncio.create_task(s._run_pipeline(b"\x01\x02" * 8000))
        s.outdoor._output_generation.reset(token)
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.001)
        assert entered.is_set()
        assert not task.done(), "speaker verification blocked the event loop"
        s.outdoor.generation += 1
        release.set()
        assert await task == "cancelled"
        assert not s.is_sleeping
        s.speaker_verifier.verify.assert_called_once()
    finally:
        release.set()
        await s.close()


@pytest.mark.asyncio
async def test_sleeping_v1_never_logs_private_transcription(caplog):
    import logging
    s, ws = await connect(caps=False)
    s.is_sleeping = True
    s.stt.transcribe.return_value = "private sleeping transcription"
    try:
        with caplog.at_level(logging.INFO):
            await s._run_pipeline(b"\x01\x02" * 8000)
        assert "private sleeping transcription" not in caplog.text
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_generation_change_during_subtitle_send_blocks_local_effects():
    s, ws = await connect()
    s.stt.transcribe.return_value = "tidur dulu"
    s.hub = Hub()
    original = ws.send_text

    async def send(text):
        import json
        await original(text)
        if json.loads(text).get("type") == "transcription":
            s.outdoor.generation += 1

    ws.send_text = send
    try:
        await control(s, "conversation_resume")
        token = s.outdoor._output_generation.set(s.outdoor.generation)
        try:
            assert await s._run_pipeline(b"\x01\x02" * 8000) == "cancelled"
        finally:
            s.outdoor._output_generation.reset(token)
        assert not s.is_sleeping
        assert not any(e["type"] == "transcription" for e in s.hub.events)
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_late_tts_chunk_cannot_advance_playback_or_publish_final_reply():
    s, ws = await connect()
    s.stt.transcribe.return_value = "Jelaskan cuaca"
    entered, release = asyncio.Event(), asyncio.Event()

    async def replies(text):
        yield "Cerah."

    async def audio(text):
        entered.set()
        await release.wait()
        yield b"\x01\0" * 20

    s.hermes.query_stream = replies
    s.tts.synthesize_stream = audio
    s.hub = Hub()
    try:
        await control(s, "conversation_resume")
        token = s.outdoor._output_generation.set(s.outdoor.generation)
        task = asyncio.create_task(s._run_outdoor_pipeline(b"\x01\x02" * 8000))
        s.outdoor._output_generation.reset(token)
        await entered.wait()
        s.hub.events.clear()
        s.outdoor.generation += 1
        release.set()
        assert await task == "cancelled"
        assert s._playback_until == 0
        assert not any(f.get("type") == "tts_end" for f in ws.frames if isinstance(f, dict))
        assert not any(isinstance(f, bytes) for f in ws.frames)
        assert s.hub.events == []
    finally:
        release.set()
        await s.close()


@pytest.mark.asyncio
async def test_actual_upload_cancel_resistant_stt_is_drained_before_owner_grant():
    import hashlib
    import struct
    import uuid
    s, ws = await connect()
    entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def transcribe(pcm):
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
        return "tidur dulu"

    s.stt.transcribe = transcribe
    s.hub = Hub()
    try:
        await control(s, "conversation_resume")
        pcm = b"\x01\x02" * 2000
        u = uid()
        meta = dict(utterance_id=u, total_samples=len(pcm)//2,
                    sha256=hashlib.sha256(pcm).hexdigest())
        await control(s, "utterance_begin", purpose="conversation", end_reason="manual_finish", **meta)
        await s.handle_binary_frame(struct.pack(">4s16sII", b"NXA2", uuid.UUID(u).bytes, 0, len(pcm)) + pcm)
        await control(s, "utterance_commit", **meta)
        await entered.wait()
        worker = s.outdoor._worker
        s.hub.events.clear()
        acquiring = asyncio.create_task(control(s, "audio_owner_acquire", owner_id=uid(),
                                                purpose="diagnostic", policy="cancel_active"))
        await cancelled.wait()
        release.set()
        await acquiring
        assert worker.done()
        assert await worker == "cancelled"
        assert ws.frames[-1]["state"] == "granted"
        assert not s.is_sleeping
        assert s.hub.events == []
        s.hermes.query_stream.assert_not_called()
    finally:
        release.set()
        await s.close()


@pytest.mark.asyncio
async def test_paused_pipeline_does_not_invoke_stt():
    s, ws = await connect()
    s.stt.transcribe.return_value = "tidur dulu"
    try:
        assert await s._run_pipeline(b"\x01\x02" * 8000) == "cancelled"
        s.stt.transcribe.assert_not_called()
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_upload_reservation_refuses_external_speech():
    import hashlib
    s, ws = await connect()
    try:
        await control(s, "conversation_resume")
        await control(s, "utterance_begin", utterance_id=uid(), purpose="conversation",
                      total_samples=1, sha256=hashlib.sha256(b"\0\0").hexdigest(), end_reason="manual_finish")
        assert s._busy()
        assert await s.speak_text("must not race upload") is False
    finally:
        await s.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("pcm,text,expected", [(b"\0\0" * 2000, "", "no_speech"),
                                             (b"\x01\x02" * 2000, "", "stt_empty")])
async def test_pipeline_distinguishes_no_speech_from_stt_empty(pcm, text, expected):
    s, ws = await connect()
    s.stt.transcribe.return_value = text
    try:
        await control(s, "conversation_resume")
        assert await s._run_pipeline(pcm) == expected
        if expected == "no_speech":
            s.stt.transcribe.assert_not_called()
        s.hermes.query.assert_not_called()
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_faster_whisper_stt_preserves_halo_nexo_and_filters_prompt_echo():
    from types import SimpleNamespace
    from nexo_server.pipeline.stt import FasterWhisperSTT

    stt = FasterWhisperSTT()
    pcm = b"\x10\x00" * 2000

    class FakeModel:
        def __init__(self, segments):
            self._segments = segments

        def transcribe(self, *args, **kwargs):
            return self._segments, None

    stt._model = FakeModel([SimpleNamespace(text=" Halo Nexo. ", no_speech_prob=0.08)])
    assert await stt.transcribe(pcm) == "Halo Nexo."

    stt._model = FakeModel([SimpleNamespace(text="Halo Nexo. Daftarkan suara, hapus sidik suara, rekam suara.", no_speech_prob=0.10)])
    assert await stt.transcribe(pcm) == ""

    stt._model = FakeModel([SimpleNamespace(text="Halo Nexo.", no_speech_prob=0.88)])
    assert await stt.transcribe(pcm) == ""
