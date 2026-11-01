# pyright: reportMissingImports=false
"""Comprehensive tests for Nexo Session Hub, Session Endpoints, and Terminal Bridge."""

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from nexo_server.main import app
from nexo_server.session_hub import SessionHub, hub
from nexo_server.session import (
    DEFAULT_SERVER_URL,
    NexoSessionClient,
    find_latest_hermes_session,
    resolve_session_id,
    get_persisted_session,
    persist_session_link,
)
from nexo_server.models.messages import NexoState


@pytest.fixture(autouse=True)
def reset_hub():
    """Reset hub state before and after each test."""
    hub._active_sessions.clear()
    hub._event_history.clear()
    hub._subscribers.clear()
    hub.set_session_thread("nexo")
    hub.set_hermes_bridge(None)
    hub.set_tts_engine(None)
    hub.set_stt_engine(None)
    yield
    hub._active_sessions.clear()
    hub._event_history.clear()
    hub._subscribers.clear()


def test_session_hub_init_and_status():
    """SessionHub should initialize with default status and thread."""
    sh = SessionHub()
    status = sh.get_status()
    assert status["appliance_connected"] is False
    assert status["active_sessions_count"] == 0
    assert status["session_thread"] == "nexo"
    assert status["state"] == "IDLE"
    assert status["active_subscribers"] == 0


def test_session_hub_session_registration():
    """SessionHub registers and unregisters appliance sessions correctly."""
    sh = SessionHub()
    mock_session = MagicMock()
    mock_session.session_id = "sess_test_123"
    mock_session.authenticated_device_id = "tablet_test"
    mock_session.ready = True
    mock_session._closed = False
    mock_session.state = NexoState.IDLE

    sh.register_session(mock_session)
    assert sh.get_active_appliance() == mock_session
    status = sh.get_status()
    assert status["appliance_connected"] is True
    assert status["active_sessions_count"] == 1
    assert status["device_id"] == "tablet_test"

    sh.unregister_session(mock_session)
    assert sh.get_active_appliance() is None
    status = sh.get_status()
    assert status["appliance_connected"] is False
    assert status["active_sessions_count"] == 0


def test_session_hub_linking():
    """SessionHub sets session thread and broadcasts session_linked event."""
    sh = SessionHub()
    q = sh.subscribe()

    name = sh.set_session_thread("dev_thread")
    assert name == "dev_thread"
    assert sh.current_session_thread == "dev_thread"

    event = q.get_nowait()
    assert event["type"] == "session_linked"
    assert event["session_name"] == "dev_thread"

    sh.unsubscribe(q)


@pytest.mark.asyncio
async def test_session_hub_inject_message():
    """Injecting message passes prompt to Hermes bridge and broadcasts events."""
    sh = SessionHub()
    mock_bridge = MagicMock()
    mock_bridge.query = AsyncMock(return_value="Halo dari Nexo!")
    mock_bridge.session_name = "nexo"
    sh.set_hermes_bridge(mock_bridge)

    q = sh.subscribe()

    res = await sh.inject_message("Halo Nexo", session_name="test_session")
    assert res["status"] == "ok"
    assert res["reply"] == "Halo dari Nexo!"
    assert res["session_name"] == "test_session"

    events = []
    while not q.empty():
        events.append(q.get_nowait())

    types = [e["type"] for e in events]
    assert "transcription" in types
    assert "cognitive" in types

    # Check transcript history
    transcripts = sh.get_transcript(limit=10)
    assert len(transcripts) == 2
    assert transcripts[0]["role"] == "user"
    assert transcripts[0]["text"] == "Halo Nexo"
    assert transcripts[1]["role"] == "assistant"
    assert transcripts[1]["text"] == "Halo dari Nexo!"


@pytest.mark.asyncio
async def test_session_hub_inject_mascot_command():
    """Expression commands bypass Hermes query and change expression."""
    sh = SessionHub()
    mock_bridge = MagicMock()
    mock_bridge.query = AsyncMock()
    sh.set_hermes_bridge(mock_bridge)

    res = await sh.inject_message("nexo tersenyum")
    assert res["status"] == "ok"
    assert res["expression"] == "senang"
    mock_bridge.query.assert_not_called()


@pytest.mark.asyncio
async def test_session_hub_trigger_speech_no_appliance():
    """Trigger speech returns error when no appliance is connected."""
    sh = SessionHub()
    res = await sh.trigger_speech("Tes bicara")
    assert res["status"] == "error"
    assert res["spoken"] is False


@pytest.mark.asyncio
async def test_session_hub_trigger_speech_with_appliance():
    """Trigger speech delegates to connected appliance session."""
    sh = SessionHub()
    mock_session = MagicMock()
    mock_session.session_id = "sess_test_1"
    mock_session.ready = True
    mock_session._closed = False
    mock_session.speak_text = AsyncMock(return_value=True)

    sh.register_session(mock_session)
    res = await sh.trigger_speech("Tes audio", expression="senang")
    assert res["status"] == "ok"
    assert res["spoken"] is True
    mock_session.speak_text.assert_called_once_with("Tes audio", expression="senang")


def test_endpoint_session_status():
    """GET /session/status returns json status."""
    client = TestClient(app)
    resp = client.get("/session/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "appliance_connected" in data
    assert "session_thread" in data
    assert data["session_thread"] == "nexo"


def test_endpoint_session_link():
    """POST /session/link updates session thread."""
    client = TestClient(app)
    resp = client.post("/session/link", json={"session_name": "custom_thread"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["session_name"] == "custom_thread"
    assert hub.current_session_thread == "custom_thread"


def test_endpoint_session_send_and_transcript():
    """POST /session/send injects message and GET /session/transcript returns history."""
    mock_bridge = MagicMock()
    mock_bridge.query = AsyncMock(return_value="Respon API")
    mock_bridge.session_name = "nexo"
    hub.set_hermes_bridge(mock_bridge)

    client = TestClient(app)
    resp = client.post("/session/send", json={"message": "Halo via HTTP", "speak": False})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["reply"] == "Respon API"

    # Get transcript
    resp_tr = client.get("/session/transcript")
    assert resp_tr.status_code == 200
    tr_data = resp_tr.json()
    assert len(tr_data["transcript"]) >= 2
    assert tr_data["transcript"][0]["text"] == "Halo via HTTP"
    assert tr_data["transcript"][1]["text"] == "Respon API"


def test_endpoint_session_speak():
    """POST /session/speak attempts speech output."""
    client = TestClient(app)
    resp = client.post("/session/speak", json={"text": "Suara via HTTP"})
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data


def test_endpoint_websocket_stream():
    """WebSocket /ws/session/stream connects, receives status, and handles commands."""
    client = TestClient(app)
    with client.websocket_connect("/ws/session/stream") as ws:
        # Initial greeting is status
        first = ws.receive_json()
        assert first["type"] == "status"

        # Ping
        ws.send_json({"type": "ping", "timestamp": 12345})
        pong = ws.receive_json()
        assert pong["type"] == "pong"
        assert pong["timestamp"] == 12345

        # Link
        ws.send_json({"type": "link", "session_name": "ws_thread"})
        link_res = ws.receive_json()
        assert link_res["type"] in ("session_linked", "link_response")

        # Send command
        mock_bridge = MagicMock()
        mock_bridge.query = AsyncMock(return_value="Respon WebSocket")
        hub.set_hermes_bridge(mock_bridge)

        ws.send_json({"type": "send", "message": "Pesan WS"})
        # Consume any broadcast events until send_response
        received_send_resp = False
        for _ in range(5):
            msg = ws.receive_json()
            if msg.get("type") == "send_response":
                assert msg["reply"] == "Respon WebSocket"
                received_send_resp = True
                break
        assert received_send_resp


def test_session_persistence(tmp_path):
    """Session link persistence writes and reads session name properly."""
    custom_auth = tmp_path / "nexo-auth"
    custom_auth.mkdir(parents=True)
    with patch("nexo_server.session._get_session_link_path", return_value=custom_auth / "session_link.json"):
        persist_session_link("thread_persisted")
        loaded = get_persisted_session()
        assert loaded == "thread_persisted"


def test_session_id_resolution():
    """Resolve session ID distinguishes IDs from multi-word session titles."""
    # Canonical local session ID
    sid, prof = resolve_session_id("local-f84c8817755ad81acbd8154cb2880ea7b5fbb407ab521771d7ac757fcce4a709")
    assert sid.startswith("local-")

    # Mock subprocess for title lookup
    fake_list = (
        "Title                        Workspace          Last Active   ID\n"
        "──────────────────────────────────────────────────────────────────\n"
        "Implementasi Terminal & Lo   Nexo             just now      local-f84c8817755ad81acbd8154cb2880ea7b5fbb407ab521771d7ac757fcce4a709\n"
    )
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = fake_list

    with patch("subprocess.run", return_value=mock_proc):
        res_id, _ = resolve_session_id("Implementasi Terminal & Lo")
        assert res_id == "local-f84c8817755ad81acbd8154cb2880ea7b5fbb407ab521771d7ac757fcce4a709"


def test_nexo_dual_compatibility_reexports():
    """Modules under nexo_server must re-export nexo_server session classes."""
    import nexo_server.session as j_sess
    import nexo_server.session_hub as j_hub

    assert hasattr(j_sess, "NexoSessionClient")
    assert hasattr(j_sess, "NexoSessionClient")
    assert j_sess.NexoSessionClient is NexoSessionClient
    assert j_sess.NexoSessionClient is NexoSessionClient

    assert hasattr(j_hub, "SessionHub")
    assert hasattr(j_hub, "hub")
    assert j_hub.SessionHub is SessionHub
