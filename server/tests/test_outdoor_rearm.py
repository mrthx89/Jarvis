"""A cancelled TTS envelope cannot suppress the next explicit turn's start."""
import asyncio
from unittest.mock import Mock
import pytest
from tests.test_outdoor_gateway import connect, control, uid


@pytest.mark.asyncio
async def test_acquire_clears_cancelled_pipeline_playback_state_before_next_resume():
    s, ws = await connect()
    entered = asyncio.Event()
    async def replies(text):
        yield "Fixture."
    async def audio(text):
        entered.set()
        await asyncio.Event().wait()
        yield b"\0\0"
    s.stt.transcribe.return_value = "pertanyaan fixture"
    s.hermes.query_stream = Mock(side_effect=replies)
    s.tts.synthesize_stream = Mock(side_effect=audio)
    try:
        await control(s, "conversation_resume")
        # The audio producer runs ahead; wait for the actual start envelope.
        task = asyncio.create_task(s._run_outdoor_pipeline(b"\x01\x02" * 8000))
        s.outdoor._worker = task
        await entered.wait()
        for _ in range(20):
            if s._tts_started:
                break
            await asyncio.sleep(0)
        assert s._tts_started
        s._playback_until = 10000000000
        s._cooldown_until = 10000000000
        await control(s, "audio_owner_acquire", owner_id=uid(), purpose="diagnostic", policy="cancel_active")
        assert task.done()
        assert not s._tts_started
        assert s._playback_until == s._cooldown_until == 0
    finally:
        await s.close()
