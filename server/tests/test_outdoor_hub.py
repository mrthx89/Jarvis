"""Terminal query routes participate in exclusive device ownership."""
import asyncio
import hashlib
from unittest.mock import AsyncMock
from types import SimpleNamespace
import pytest
from nexo_server.session_hub import SessionHub
from tests.test_outdoor_gateway import connect, control, uid


@pytest.mark.asyncio
@pytest.mark.parametrize("busy_state", ["paused", "owned", "uploading", "processing"])
async def test_busy_appliance_refuses_terminal_query_without_hermes_or_events(busy_state):
    s, ws = await connect()
    hub = SessionHub()
    hub.register_session(s)
    hub._hermes_bridge = SimpleNamespace(query=AsyncMock(return_value="fixture reply"))
    try:
        if busy_state == "owned":
            await control(s, "audio_owner_acquire", owner_id=uid(), purpose="diagnostic", policy="cancel_active")
        elif busy_state in {"uploading", "processing"}:
            await control(s, "conversation_resume")
            assert s.outdoor is not None
            if busy_state == "uploading":
                pcm = b"\x00\x00" * 16000
                await control(s, "utterance_begin", purpose="conversation", end_reason="manual_finish",
                              utterance_id=uid(), total_samples=16000, sha256=hashlib.sha256(pcm).hexdigest())
                assert s.outdoor.assembly is not None
            else:
                s.outdoor.active_task = asyncio.create_task(asyncio.Event().wait())
        history = list(hub._event_history)
        frames = list(ws.frames)
        assert await hub.inject_message("fixture", speak=True) == {
            "status": "busy", "spoken": False, "device_spoken": False,
        }
        hub._hermes_bridge.query.assert_not_called()
        assert hub._event_history == history
        assert ws.frames == frames
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_owner_cancels_terminal_delivery_without_cancelling_terminal_service():
    s, ws = await connect()
    hub = SessionHub()
    hub.register_session(s)
    started, returned, stop = asyncio.Event(), asyncio.Event(), asyncio.Event()
    results = []

    async def query(message):
        started.set()
        await asyncio.Event().wait()

    async def terminal_service():
        results.append(await hub.inject_message("fixture", speak=True))
        returned.set()
        await stop.wait()

    hub._hermes_bridge = SimpleNamespace(query=query)
    service = None
    try:
        await control(s, "conversation_resume")
        service = asyncio.create_task(terminal_service())
        await asyncio.wait_for(started.wait(), 1)
        history = list(hub._event_history)
        await control(s, "audio_owner_acquire", owner_id=uid(), purpose="diagnostic", policy="cancel_active")
        await asyncio.wait_for(returned.wait(), 1)
        assert results == [{"status": "busy", "spoken": False, "device_spoken": False}]
        assert not service.done()
        assert hub._event_history == history
        assert not s._output_tasks
    finally:
        stop.set()
        if service is not None:
            service.cancel()
            await asyncio.gather(service, return_exceptions=True)
        await s.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["query", "failed_query", "stream", "empty_stream", "failed_stream"])
async def test_explicit_resume_fences_cancel_resistant_terminal_result(mode):
    s, ws = await connect()
    hub = SessionHub()
    hub.register_session(s)
    started, cancelled, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
    fallback = AsyncMock(return_value="duplicate fallback")

    async def synthesize_stream(text: str, voice: str = "id-ID-ArdiNeural"):
        yield b"\x00\x00" * 160

    s.tts.synthesize_stream = synthesize_stream

    async def pending_reply():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            task = asyncio.current_task()
            assert task is not None
            task.uncancel()
            cancelled.set()
            await finish.wait()
        return "stale reply"

    async def query(message):
        reply = await pending_reply()
        if mode == "failed_query":
            raise RuntimeError("late query failure")
        return reply

    async def query_stream(message):
        reply = await pending_reply()
        if mode == "failed_stream":
            raise RuntimeError("late stream failure")
        if mode == "stream":
            yield reply

    hub._hermes_bridge = (SimpleNamespace(query=query) if mode in {"query", "failed_query"} else
                          SimpleNamespace(query=fallback, query_stream=query_stream))
    delivery = resume = None
    try:
        await control(s, "conversation_resume")
        delivery = asyncio.create_task(hub.inject_message("fixture", speak=True))
        await asyncio.wait_for(started.wait(), 1)
        history = list(hub._event_history)
        frames = list(ws.frames)
        resume = asyncio.create_task(control(s, "conversation_resume"))
        await asyncio.wait_for(cancelled.wait(), 1)
        finish.set()
        result = await asyncio.wait_for(delivery, 1)
        await asyncio.wait_for(resume, 1)
        assert result == {"status": "busy", "spoken": False, "device_spoken": False}
        assert hub._event_history == history
        assert len(ws.frames) == len(frames) + 1
        assert ws.frames[-1]["type"] == "conversation_resumed"
        fallback.assert_not_called()
        assert not s._output_tasks
    finally:
        finish.set()
        for task in (delivery, resume):
            if task is not None:
                task.cancel()
        await asyncio.gather(*(t for t in (delivery, resume) if t is not None), return_exceptions=True)
        await s.close()


@pytest.mark.asyncio
async def test_terminal_caller_cancellation_propagates_and_stops_resistant_delivery():
    s, ws = await connect()
    hub = SessionHub()
    hub.register_session(s)
    started, cancelled, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def query(message):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await finish.wait()
        return "cancelled caller reply"

    hub._hermes_bridge = SimpleNamespace(query=query)
    caller = None
    children = []
    try:
        await control(s, "conversation_resume")
        caller = asyncio.create_task(hub.inject_message("fixture"))
        await asyncio.wait_for(started.wait(), 1)
        children = list(s._output_tasks)
        history = list(hub._event_history)
        frames = list(ws.frames)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        await asyncio.wait_for(cancelled.wait(), 1)
        finish.set()
        await asyncio.wait_for(asyncio.gather(*children, return_exceptions=True), 1)
        assert hub._event_history == history
        assert ws.frames == frames
        assert not s._output_tasks
    finally:
        finish.set()
        if caller is not None:
            caller.cancel()
        await asyncio.gather(*(t for t in [caller, *children] if t is not None), return_exceptions=True)
        await s.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("v2", [False, True], ids=["v1", "v2"])
async def test_allowed_terminal_query_preserves_reply_and_session_validation(v2):
    s, ws = await connect(caps=v2)
    hub = SessionHub()
    hub.register_session(s)
    hub._hermes_bridge = SimpleNamespace(query=AsyncMock(return_value="fixture reply"))
    try:
        if v2:
            await control(s, "conversation_resume")
        else:
            s._enrollment = {}
        history = list(hub._event_history)
        with pytest.raises(ValueError, match="locked to nexo"):
            await hub.inject_message("fixture", session_name="other")
        assert hub._event_history == history
        hub._hermes_bridge.query.assert_not_called()
        result = await hub.inject_message("fixture")
        assert result["status"] == "ok"
        assert result["reply"] == "fixture reply"
        assert result["spoken"] is False
        hub._hermes_bridge.query.assert_awaited_once_with("fixture")
        assert [event["text"] for event in hub.get_transcript()] == ["fixture", "fixture reply"]
        assert not s._output_tasks
    finally:
        s._enrollment = None
        await s.close()
