"""Lifecycle starts/stops the server-owned observer, never a second model."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import nexo_server.main as main


@pytest.mark.asyncio
async def test_cli_lifespan_binds_canonical_id_and_cancels_observer(monkeypatch, tmp_path):
    import nexo_server.session_store as store
    import nexo_server.answer_mirror as mirror_module

    calls = []
    bridge = SimpleNamespace(bind_session_id=lambda sid: calls.append(sid), on_reply=None)
    monkeypatch.setattr(main, 'hermes_bridge', bridge)
    monkeypatch.setattr(main.config.hermes, 'bridge_mode', 'cli')
    monkeypatch.setattr(main.config.hermes, 'hermes_home', str(tmp_path))
    monkeypatch.setattr(main.config.hermes, 'session_binding_path', str(tmp_path / 'binding.json'))
    monkeypatch.setattr(store, 'resolve_pinned_session', lambda *args: 'local-fixed')

    class Reader:
        def __init__(self, db, sid):
            calls.append((db.name, sid))

        def initialize(self):
            calls.append('initialized')

    class Mirror:
        def __init__(self, reader, hub, bridge):
            self.cancelled = False
            calls.append(self)

        async def on_bridge_reply(self, text):
            pass

        async def run(self):
            import asyncio
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True

    monkeypatch.setattr(store, 'HermesAnswerReader', Reader)
    monkeypatch.setattr(mirror_module, 'AnswerMirror', Mirror)
    monkeypatch.setattr(main.hub, 'set_hermes_bridge', lambda b: None)
    async with main.lifespan(main.app):
        import asyncio
        await asyncio.sleep(0)
        assert 'local-fixed' in calls
        assert ('state.db', 'local-fixed') in calls
        assert 'initialized' in calls
        assert callable(bridge.on_reply)
    assert next(c for c in calls if isinstance(c, Mirror)).cancelled
