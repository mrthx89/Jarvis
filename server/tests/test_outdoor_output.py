"""Actual gateway output barriers during dedicated ownership."""
import asyncio
import pytest
from tests.test_outdoor_gateway import connect, control, uid
from nexo_server.models.messages import NexoState, TTSStart, VoiceprintDialog
from unittest.mock import Mock


@pytest.mark.asyncio
async def test_outdoor_wrapper_ends_tts_before_runtime_resumes_listening():
    s, ws = await connect()
    s.stt.transcribe.return_value = "Bagaimana cuaca?"

    async def replies(text):
        yield "Cuacanya cerah."

    async def audio(text):
        yield b"\0\0" * 20

    s.hermes.query_stream = Mock(side_effect=replies)
    s.tts.synthesize_stream = Mock(side_effect=audio)
    try:
        await control(s, "conversation_resume")
        result = await s._run_outdoor_pipeline(b"\x01\x02" * 8000)
        assert result in (None, "ok")
        assert not s._tts_started
        kinds = [f["type"] for f in ws.frames if isinstance(f, dict)]
        assert kinds.count("tts_start") == kinds.count("tts_end") == 1
        assert kinds[-1] == "tts_end"
        start = next(f for f in ws.frames if isinstance(f, dict) and f["type"] == "tts_start")
        assert start["bit_depth"] == 16
        assert start["connection_id"] == s.outdoor.connection_id
        assert start["output_epoch"] == s.outdoor.epoch
    finally:
        await s.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("caps", [False, True])
async def test_real_turn_streams_pcm_and_ends_before_idle(caps):
    import hashlib
    import struct
    import uuid
    s, ws = await connect(caps=caps)
    s.ECHO_GUARD_SECONDS = 0
    s.stt.transcribe.return_value = "Bagaimana cuaca?"

    async def replies(text):
        yield "Cerah."
        yield "Tetap hati-hati."

    async def audio(text):
        yield b"\x01\x00" * 20

    s.hermes.query_stream = Mock(side_effect=replies)
    s.tts.synthesize_stream = Mock(side_effect=audio)
    try:
        pcm = b"\x01\x02" * 8000
        if caps:
            await control(s, "conversation_resume")
            u = uid()
            meta = dict(utterance_id=u, total_samples=len(pcm)//2,
                        sha256=hashlib.sha256(pcm).hexdigest())
            await control(s, "utterance_begin", purpose="conversation", end_reason="manual_finish", **meta)
            for offset in range(0, len(pcm), 8000):
                part = pcm[offset:offset+8000]
                await s.handle_binary_frame(struct.pack(">4s16sII", b"NXA2", uuid.UUID(u).bytes,
                                                         offset//2, len(part)) + part)
            await control(s, "utterance_commit", **meta)
            await s.outdoor.active_task
        else:
            s.audio_buffer.extend(pcm)
            await s.process_recorded_audio()
        frames = [f for f in ws.frames if isinstance(f, dict)]
        kinds = [f["type"] for f in frames]
        assert kinds.count("tts_start") == kinds.count("tts_end") == 1
        end = kinds.index("tts_end")
        assert any(f.get("state") == "IDLE" for f in frames[end + 1:])
        assert len([f for f in ws.frames if isinstance(f, bytes)]) == 2
        assert not s._tts_started
        if caps:
            assert frames[-1]["code"] == "ok"
            assert frames[-1]["status"] == "completed"
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_sheet_suppresses_state_json_and_binary_but_controls_still_reply():
    s, ws = await connect()
    try:
        await control(s, "audio_owner_acquire", owner_id=uid(), purpose="diagnostic", policy="cancel_active")
        granted = ws.frames[-1]
        n = len(ws.frames)
        await s.send_state(NexoState.IDLE, "must not look ready")
        await s.send_json_message(TTSStart())
        assert len(ws.frames) == n
        await s.send_audio_bytes(b"\x00\x00")
        assert len(ws.frames) == n
        await control(s, "audio_owner_release", owner_id=granted["owner_id"], lease_id=granted["lease_id"], reason="cancelled")
        assert ws.frames[-1]["state"] == "released"
        assert s.outdoor.paused
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_acquire_drains_external_speech_before_grant_and_never_replays_it():
    s, ws = await connect()
    started = asyncio.Event()
    stopped = asyncio.Event()
    async def stream(text):
        started.set()
        try:
            await asyncio.Event().wait()
            yield b"unreachable"
        finally:
            stopped.set()
    s.tts.synthesize_stream = stream
    try:
        await control(s, "conversation_resume")
        speaking = asyncio.create_task(s.speak_text("fixture"))
        await started.wait()
        await control(s, "audio_owner_acquire", owner_id=uid(), purpose="diagnostic", policy="cancel_active")
        assert stopped.is_set()
        assert speaking.done()
        assert await speaking is False
        grant = ws.frames[-1]
        assert grant["state"] == "granted"
        assert grant["connection_id"] == s.outdoor.connection_id
        assert all("output_epoch" in f for f in ws.frames if isinstance(f, dict) and f["type"] not in {"server_hello", "pong"})
    finally:
        if 'speaking' in locals() and not speaking.done():
            speaking.cancel()
        await s.close()
