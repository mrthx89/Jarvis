"""Behavioral regressions for the independent outdoor-audio QA findings."""
import asyncio
from types import SimpleNamespace

import pytest

from nexo_server.session_hub import SessionHub
from tests.test_outdoor_gateway import connect, control
from tests.test_outdoor_runtime import metadata, uid
from tests.test_answer_mirror import Reader
from nexo_server.answer_mirror import AnswerMirror
import threading
from tests.test_outdoor_runtime import frame


@pytest.mark.asyncio
async def test_terminal_admission_excludes_terminal_speech_and_upload_but_allows_own_tts():
    s, ws = await connect()
    hub = SessionHub()
    hub.register_session(s)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    spoken = []

    async def query(text):
        calls.append(text)
        entered.set()
        await release.wait()
        return "fixture reply"

    async def speak(text, expression):
        spoken.append(text)
        return True

    hub._hermes_bridge = SimpleNamespace(query=query)
    s._speak_text_owned = speak
    first = None
    try:
        await control(s, "conversation_resume")
        first = asyncio.create_task(hub.inject_message("first", speak=True))
        await asyncio.wait_for(entered.wait(), 1)
        second = asyncio.create_task(hub.inject_message("second"))
        for _ in range(20):
            await asyncio.sleep(0)
        assert calls == ["first"]
        assert second.done() and second.result()["status"] == "busy"
        assert await s.speak_text("unrelated") is False
        await control(s, "utterance_begin", **metadata(b"\x01\x00"))
        assert ws.frames[-1]["code"] == "busy"
        release.set()
        assert (await first)["device_spoken"] is True
        assert spoken == ["fixture reply"]
    finally:
        release.set()
        if first:
            await first
        await s.close()


@pytest.mark.parametrize("phase", ["queued", "deferred", "reading", "speaking"])
@pytest.mark.asyncio
async def test_owner_boundary_discards_old_mirrors_but_preserves_future_finals(phase):
    s, ws = await connect()
    hub = SessionHub()
    s.hub = hub
    hub.register_session(s)
    reader = Reader()
    bridge = SimpleNamespace(query_active=False)
    mirror = AnswerMirror(reader, hub, bridge)
    entered, release = threading.Event(), threading.Event()
    task = None
    try:
        await control(s, "conversation_resume")
        reader.rows = [dict(id=1, text="old final")]
        if phase == "reading":
            read = reader.read_new
            def held_read():
                rows = read()
                entered.set()
                release.wait(2)
                return rows
            reader.read_new = held_read
            task = asyncio.create_task(mirror.poll_once())
        else:
            if phase == "deferred":
                def racing_read():
                    bridge.query_active = True
                    return Reader.read_new(reader)
                reader.read_new = racing_read
            await mirror.poll_once()
            if phase == "speaking":
                async def held_speech(text, expression):
                    entered.set()
                    await asyncio.Event().wait()
                s._speak_text_owned = held_speech
                task = asyncio.create_task(mirror.deliver_once())
        if phase in {"reading", "speaking"}:
            for _ in range(200):
                if entered.is_set():
                    break
                await asyncio.sleep(0.001)
            assert entered.is_set()
        owner_id = uid()
        await control(s, "audio_owner_acquire", owner_id=owner_id,
                      purpose="diagnostic", policy="cancel_active")
        lease_id = s.outdoor.owner["lease_id"]
        release.set()
        if task:
            await task
        assert not mirror.pending
        assert not mirror._deferred
        await control(s, "audio_owner_release", owner_id=owner_id, lease_id=lease_id)
        await control(s, "conversation_resume")
        bridge.query_active = False
        reader.read_new = lambda: Reader.read_new(reader)
        reader.rows = [dict(id=2, text="future legitimate final")]
        await mirror.poll_once()
        assert [row["id"] for row in mirror.pending] == [2]
    finally:
        release.set()
        if task:
            await task
        await s.close()
