"""Server-side routing must remain in the dedicated Nexo conversation."""

from unittest.mock import AsyncMock, MagicMock, patch

from nexo_server.session_hub import SessionHub

import pytest

from nexo_server.hermes.agent_bridge import HermesBridge


@pytest.mark.parametrize("name", ["dev", "custom", "test", "ws", "", "   "])
def test_bridge_rejects_foreign_initial_session_name(name):
    with pytest.raises(ValueError, match="nexo"):
        HermesBridge(session_name=name)


@pytest.mark.parametrize("method", ["property", "setter"])
def test_bridge_name_cannot_be_changed(method):
    bridge = HermesBridge()
    with pytest.raises(ValueError, match="nexo"):
        if method == "property":
            bridge.session_name = "foreign"
        else:
            bridge.set_session_name("foreign")
    assert bridge.session_name == bridge.get_session_name() == "nexo"
    bridge.session_name = "nexo"
    bridge.set_session_name("nexo")
    assert bridge.get_session_name() == "nexo"


def test_bridge_binds_session_id_once():
    bridge = HermesBridge()
    assert bridge.get_session_id() is None
    bridge.bind_session_id("local-canonical")
    bridge.bind_session_id("local-canonical")
    assert bridge.get_session_id() == "local-canonical"
    with pytest.raises(ValueError, match="nexo"):
        bridge.bind_session_id("local-foreign")
    assert bridge.get_session_id() == "local-canonical"


@pytest.mark.parametrize("session_id", [None, "", "   "])
def test_bridge_refuses_empty_binding(session_id):
    bridge = HermesBridge()
    with pytest.raises(ValueError):
        bridge.bind_session_id(session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [None, "nexo", "local-canonical"])
async def test_bound_bridge_queries_canonical_id_without_creating(target):
    bridge = HermesBridge()
    bridge.bind_session_id("local-canonical")
    proc = AsyncMock()
    proc.returncode = 0
    proc.communicate.return_value = (b"Answer\nsession_id: local-canonical", b"")
    with patch("asyncio.create_subprocess_exec", return_value=proc) as spawn:
        assert await bridge.query("hello", session_name=target) == "Answer"
    args = spawn.call_args.args
    assert args[args.index("-c") + 1] == "local-canonical"
    assert "--create-if-missing" not in args


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cli", "mock"])
@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("target", ["foreign", "local-other", "", "   "])
async def test_bridge_rejects_foreign_query_before_any_work(mode, bound, target):
    bridge = HermesBridge(mode=mode)
    if bound:
        bridge.bind_session_id("local-canonical")
    with patch("asyncio.create_subprocess_exec") as spawn, patch("asyncio.sleep") as sleep:
        with pytest.raises(ValueError, match="nexo"):
            await bridge.query("hello", session_name=target)
    spawn.assert_not_called()
    sleep.assert_not_called()


@pytest.mark.asyncio
async def test_unbound_bridge_defaults_to_nexo():
    bridge = HermesBridge()
    proc = AsyncMock()
    proc.returncode = 0
    proc.communicate.return_value = (b"Answer", b"")
    with patch("asyncio.create_subprocess_exec", return_value=proc) as spawn:
        assert await bridge.query("hello") == "Answer"
    args = spawn.call_args.args
    assert args[args.index("-c") + 1] == "nexo"
    assert "--create-if-missing" in args


@pytest.mark.parametrize("target", ["dev", "custom", "local-foreign", "", "   ", None])
def test_hub_link_rejects_foreign_target_without_side_effects(target):
    hub = SessionHub()
    bridge = HermesBridge()
    hub.set_hermes_bridge(bridge)
    queue = hub.subscribe()
    with pytest.raises(ValueError, match="nexo"):
        hub.set_session_thread(target)
    assert hub.current_session_thread == bridge.session_name == "nexo"
    assert hub._event_history == []
    assert queue.empty()


def test_hub_nexo_link_is_idempotent():
    hub = SessionHub()
    assert hub.set_session_thread("nexo") == "nexo"
    assert hub.set_session_thread("nexo") == "nexo"
    assert hub.current_session_thread == "nexo"


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["hello", "nexo tersenyum"])
@pytest.mark.parametrize("target", ["dev", "custom", "local-foreign", "", "   "])
async def test_hub_validates_injection_before_publication_or_device_work(message, target):
    hub = SessionHub()
    bridge = MagicMock()
    bridge.query = AsyncMock(return_value="Answer")
    hub.set_hermes_bridge(bridge)
    appliance = MagicMock()
    appliance.session_id = "device-session"
    appliance.ready = True
    appliance._closed = False
    appliance.send_state = AsyncMock()
    hub.register_session(appliance)
    hub.clear_history()
    queue = hub.subscribe()
    with pytest.raises(ValueError, match="nexo"):
        await hub.inject_message(message, session_name=target)
    assert hub._event_history == []
    assert queue.empty()
    bridge.query.assert_not_called()
    appliance.send_state.assert_not_called()


@pytest.mark.asyncio
async def test_hub_accepts_bound_id_for_injection_without_changing_thread():
    hub = SessionHub()
    bridge = HermesBridge(mode="mock")
    bridge.bind_session_id("local-canonical")
    hub.set_hermes_bridge(bridge)
    with patch("asyncio.sleep"):
        response = await hub.inject_message("hello", session_name="local-canonical")
    assert response["status"] == "ok"
    assert hub.current_session_thread == "nexo"
    with pytest.raises(ValueError):
        hub.set_session_thread("local-canonical")


@pytest.mark.parametrize("bridge_kind", ["none", "legacy", "mock", "bound"])
def test_hub_status_exposes_lock_and_only_real_session_id(bridge_kind):
    hub = SessionHub()
    if bridge_kind == "legacy":
        hub.set_hermes_bridge(object())
    elif bridge_kind == "mock":
        hub.set_hermes_bridge(MagicMock())
    elif bridge_kind == "bound":
        bridge = HermesBridge()
        bridge.bind_session_id("local-canonical")
        hub.set_hermes_bridge(bridge)
    status = hub.get_status()
    assert status["session_locked"] is True
    assert status["hermes_session_id"] == ("local-canonical" if bridge_kind == "bound" else None)
    assert status["session_thread"] == "nexo"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cli", "mock"])
async def test_bridge_reply_hook_runs_while_query_active(mode):
    bridge = HermesBridge(mode=mode)
    assert bridge.query_active == 0
    assert bridge.on_reply is None
    observed = []

    async def on_reply(reply):
        observed.append((reply, bridge.query_active))

    bridge.on_reply = on_reply
    proc = AsyncMock()
    proc.returncode = 0

    async def communicate():
        assert bridge.query_active == 1
        return b"Answer\nsession_id: canonical", b""

    proc.communicate.side_effect = communicate
    with patch("asyncio.create_subprocess_exec", return_value=proc), patch("asyncio.sleep"):
        reply = await bridge.query("hello")
    assert observed == [(reply, 1)]
    assert bridge.query_active == 0
    if mode == "cli":
        assert reply == "Answer"


@pytest.mark.asyncio
async def test_bridge_hook_failure_is_safe_and_does_not_drop_reply(caplog):
    bridge = HermesBridge(mode="mock")
    bridge.on_reply = AsyncMock(side_effect=RuntimeError("private payload"))
    with patch("asyncio.sleep"):
        reply = await bridge.query("hello")
    assert "hello" in reply
    assert bridge.query_active == 0
    assert "reply hook" in caplog.text.lower()
    assert "private payload" not in caplog.text


@pytest.mark.asyncio
async def test_bridge_child_uses_observer_hermes_home_without_changing_parent_env(monkeypatch):
    import os

    bridge = HermesBridge()
    bridge.hermes_home = "/chosen/hermes-home"
    monkeypatch.setenv("HERMES_HOME", "/inherited/home")
    monkeypatch.setenv("UNRELATED_SETTING", "keep")
    proc = AsyncMock()
    proc.returncode = 0
    proc.communicate.return_value = (b"Answer", b"")
    with patch("asyncio.create_subprocess_exec", return_value=proc) as spawn:
        await bridge.query("hello")
    env = spawn.call_args.kwargs["env"]
    assert env["HERMES_HOME"] == "/chosen/hermes-home"
    assert env["UNRELATED_SETTING"] == "keep"
    assert os.environ["HERMES_HOME"] == "/inherited/home"


@pytest.mark.asyncio
async def test_bridge_query_cancellation_clears_active_count():
    import asyncio

    bridge = HermesBridge(mode="mock")
    with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            await bridge.query("hello")
    assert bridge.query_active == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["nexo-device_02", "nexo-hp_kedua", "nexo-tablet_secondary"])
async def test_bridge_allows_isolated_device_sessions(target):
    bridge = HermesBridge()
    proc = AsyncMock()
    proc.returncode = 0
    proc.communicate.return_value = (b"Device reply", b"")
    with patch("asyncio.create_subprocess_exec", return_value=proc) as spawn:
        reply = await bridge.query("halo", session_name=target)
        assert reply == "Device reply"
    args = spawn.call_args.args
    assert args[args.index("-c") + 1] == target
    assert "--create-if-missing" in args
