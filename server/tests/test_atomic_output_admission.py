"""Admission stays reserved across acceptance writes and cancellation cleanup."""
import asyncio

import pytest

from nexo_server.session_hub import SessionHub
from tests.test_outdoor_gateway import connect, control
from tests.test_outdoor_runtime import frame, metadata


@pytest.mark.asyncio
async def test_integrity_ack_latency_does_not_open_terminal_or_speech_admission():
    s, ws = await connect()
    hub = SessionHub()
    hub.register_session(s)
    entered, release = asyncio.Event(), asyncio.Event()
    commit = None
    original = s.send_json_message
    async def held_ack(message):
        await original(message)
        if isinstance(message, dict) and message.get("stage") == "accepted":
            entered.set()
            await release.wait()
    s.send_json_message = held_ack
    try:
        await control(s, "conversation_resume")
        pcm = b"\x01\x00"
        meta = metadata(pcm)
        await control(s, "utterance_begin", **meta)
        await s.handle_binary_frame(frame(meta["utterance_id"], 0, pcm))
        commit = asyncio.create_task(control(s, "utterance_commit", **{k: meta[k] for k in
                                      ("utterance_id", "total_samples", "sha256")}))
        await asyncio.wait_for(entered.wait(), 1)
        assert (await hub.inject_message("unrelated"))["status"] == "busy"
        assert await s.speak_text("unrelated") is False
    finally:
        release.set()
        if commit:
            await commit
        await s.close()


@pytest.mark.asyncio
async def test_cancelled_external_caller_keeps_reservation_until_tts_cleanup_stops():
    s, ws = await connect()
    hub = SessionHub()
    hub.register_session(s)
    entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def speak(text, expression):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
        return False
    s._speak_text_owned = speak
    speech = None
    try:
        await control(s, "conversation_resume")
        speech = asyncio.create_task(s.speak_text("first"))
        await asyncio.wait_for(entered.wait(), 1)
        speech.cancel()
        await cancelled.wait()
        with pytest.raises(asyncio.CancelledError):
            await speech
        assert s._busy()
        assert (await hub.inject_message("second"))["status"] == "busy"
        release.set()
        await asyncio.gather(*s._output_tasks, return_exceptions=True)
        assert not s._busy()
    finally:
        release.set()
        await s.close()
