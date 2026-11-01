"""Real SQLite observer -> recording appliance, with deterministic providers only."""
import asyncio
import sqlite3
import threading
from types import SimpleNamespace
from typing import Any, cast

import pytest

from nexo_server.answer_mirror import AnswerMirror
from nexo_server.hermes.agent_bridge import HermesBridge
from nexo_server.session_hub import SessionHub
from nexo_server.pipeline.stt import BaseSTT
from nexo_server.session_store import HermesAnswerReader, resolve_pinned_session
from tests.test_session_store import db_path, message, session
from tests.test_voice_reception import make_session


@pytest.mark.asyncio
@pytest.mark.parametrize("read_started_online", [True, False])
async def test_reconnect_during_reader_collection_never_replays_offline_history(
        db_path, read_started_online):
    reader = HermesAnswerReader(db_path, "canonical")
    reader.initialize()
    hub = SessionHub()
    bridge = SimpleNamespace(query_active=0)
    mirror = AnswerMirror(reader, hub, bridge)
    device = make_session()
    device.ECHO_GUARD_SECONDS = 0
    if read_started_online:
        hub.register_session(device)
    entered, release = threading.Event(), threading.Event()
    read_new = reader.read_new

    def paused_read():
        entered.set()
        assert release.wait(5)
        return read_new()

    reader.read_new = paused_read
    task = asyncio.create_task(mirror.poll_once())
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        if read_started_online:
            hub.unregister_session(device)
        for _ in range(101):
            message(db_path, role="tool")
        message(db_path, text="While OFF behind first batch")
        hub.register_session(device)
        release.set()
        await task
        reader.read_new = read_new
        await mirror.poll_once()
        await mirror.deliver_once()
        assert not cast(Any, device.websocket).messages
        assert not mirror.pending
        message(db_path, text="Fresh after reconnect")
        await mirror.poll_once()
        await mirror.deliver_once()
        assert [m["text"] for m in cast(Any, device.websocket).messages
                if m["type"] == "transcription"] == ["Fresh after reconnect"]
    finally:
        release.set()
        await task
        await device.close()


@pytest.mark.asyncio
async def test_reply_suppression_highwater_is_captured_before_async_read(db_path):
    reader = HermesAnswerReader(db_path, "canonical")
    reader.initialize()
    hub = SessionHub()
    mirror = AnswerMirror(reader, hub, SimpleNamespace(query_active=1))
    message(db_path, text="Same answer")
    entered, release = threading.Event(), threading.Event()
    read_new = reader.read_new

    def paused_read():
        entered.set()
        assert release.wait(5)
        return read_new()

    reader.read_new = paused_read
    task = asyncio.create_task(mirror.on_bridge_reply("Same answer"))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        terminal_id = message(db_path, text="Same answer")
        release.set()
        await task
        assert [row["id"] for row in mirror.pending] == [terminal_id]
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_owned_final_after_multiple_batches_is_suppressed_without_losing_terminal_finals(db_path):
    reader = HermesAnswerReader(db_path, "canonical")
    reader.initialize()
    hub = SessionHub()
    bridge = SimpleNamespace(query_active=1)
    mirror = AnswerMirror(reader, hub, bridge)
    device = make_session()
    hub.register_session(device)
    try:
        # Same text belongs to a terminal turn; only the LAST matching final
        # persisted before the hook is owned by this bridge reply.
        message(db_path, text="Voice final")
        message(db_path, text="Terminal before")
        for _ in range(201):
            message(db_path, role="tool")
        message(db_path, text="Voice final")
        message(db_path, text="Terminal after")
        await mirror.on_bridge_reply("Voice final")
        bridge.query_active = 0
        for _ in range(4):
            await mirror.poll_once()
        assert [row["text"] for row in mirror.pending] == [
            "Voice final", "Terminal before", "Terminal after"]
        message(db_path, text="Voice final")  # A later terminal turn is not owned.
        await mirror.poll_once()
        assert [row["text"] for row in mirror.pending] == [
            "Voice final", "Terminal before", "Terminal after", "Voice final"]
    finally:
        await device.close()


@pytest.mark.asyncio
async def test_pinned_terminal_and_voice_finals_deliver_once_without_foreign_output(db_path, tmp_path):
    session(db_path)
    session(db_path, "foreign", title="another")
    sid = resolve_pinned_session(db_path, tmp_path / "binding" / "session.json")
    bridge = HermesBridge()
    bridge.bind_session_id(sid)
    hub = SessionHub()
    hub.set_hermes_bridge(bridge)
    reader = HermesAnswerReader(db_path, sid)
    reader.initialize()
    mirror = AnswerMirror(reader, hub, bridge)
    bridge.on_reply = mirror.on_bridge_reply
    device = make_session()
    socket = cast(Any, device.websocket)
    device.ECHO_GUARD_SECONDS = 0
    device.hermes = bridge
    device.hub = hub
    hub.register_session(device)
    queries = []

    async def provider(prompt, active_session):
        queries.append((prompt, active_session))
        message(db_path, text="Voice final", sid=active_session)
        return "Voice final"

    bridge._query = provider

    class STT(BaseSTT):
        async def transcribe(self, pcm_audio, language="id"):
            return "Jelaskan rangkaian ini"

    device.stt = STT()
    try:
        message(db_path, text="Terminal final")
        message(db_path, text="Foreign final", sid="foreign")
        await mirror.poll_once()
        await mirror.deliver_once()
        assert queries == []  # Observer never queries Hermes.
        await device._run_pipeline(bytes(3200))
        await device._resume_listening()
        await mirror.poll_once()
        await mirror.deliver_once()
        finals = [m["text"] for m in socket.messages
                  if m["type"] == "transcription" and m["role"] == "assistant"]
        assert finals == ["Terminal final", "Voice final"]
        assert sum(m["type"] == "tts_start" for m in socket.messages) == 2
        assert queries == [("Jelaskan rangkaian ini", "canonical")]
        # Session rename and a newly created namesake cannot move the binding.
        with sqlite3.connect(db_path) as db:
            db.execute("UPDATE sessions SET title = 'renamed' WHERE id = 'canonical'")
        session(db_path, "replacement")
        assert resolve_pinned_session(db_path, tmp_path / "binding" / "session.json") == sid
        # OFF/disconnect must discard rather than replay on reconnect.
        hub.unregister_session(device)
        message(db_path, text="While OFF")
        await mirror.poll_once()
        await mirror.deliver_once()
        hub.register_session(device)
        await mirror.deliver_once()
        assert not mirror.pending
        assert [m["text"] for m in socket.messages
                if m["type"] == "transcription" and m["role"] == "assistant"] == finals
    finally:
        await device.close()
