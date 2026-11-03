"""Actual serialized frames retain per-dispatch identity across async TTS."""
from types import SimpleNamespace

import pytest

from tests.test_outdoor_gateway import connect, control
from tests.test_outdoor_runtime import frame, metadata


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.asyncio
async def test_consecutive_turns_same_epoch_have_immutable_ids_and_external_speech_has_none(streaming):
    s, ws = await connect()
    s.ECHO_GUARD_SECONDS = 0
    s.stt.transcribe.return_value = "Jelaskan cuaca"
    async def query(text):
        return "Cerah."
    async def stream(text):
        yield "Cerah."
    async def audio(text):
        yield b"\x00\x00"
    s.hermes = SimpleNamespace(**({"query_stream": stream} if streaming else {"query": query}))
    s.tts.synthesize_stream = audio
    kinds = {"state_change", "transcription", "tts_start", "tts_end", "visual_telemetry"}
    try:
        await control(s, "conversation_resume")
        epoch = s.outdoor.epoch
        for _ in range(2):
            pcm = b"\x01\x02" * 2000
            meta = metadata(pcm)
            start = len(ws.frames)
            await control(s, "utterance_begin", **meta)
            await s.handle_binary_frame(frame(meta["utterance_id"], 0, pcm))
            await control(s, "utterance_commit", **{k: meta[k] for k in
                          ("utterance_id", "total_samples", "sha256")})
            await s.outdoor.active_task
            derived = [f for f in ws.frames[start:] if isinstance(f, dict) and f["type"] in kinds]
            assert {f["type"] for f in derived} >= {"state_change", "transcription", "tts_start", "tts_end"}
            assert all(f.get("utterance_id") == meta["utterance_id"] for f in derived)
            assert all(f["output_epoch"] == epoch for f in derived)
        start = len(ws.frames)
        token = s.outdoor.output_utterance.set(meta["utterance_id"])
        try:
            assert await s.speak_text("external")
        finally:
            s.outdoor.output_utterance.reset(token)
        external = [f for f in ws.frames[start:] if isinstance(f, dict) and f["type"] in kinds]
        assert external and all("utterance_id" not in f for f in external)
    finally:
        await s.close()
