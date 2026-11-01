"""Private worker boundary tests: no real Telegram calls or credentials."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from nexo_server.telegram_command import read_settings, send_protected


def _get_plugin_path():
    nexo_plugin = Path(__file__).resolve().parents[1] / 'hermes-plugin/nexo-device-access/__init__.py'
    if nexo_plugin.exists():
        return nexo_plugin
    return Path(__file__).resolve().parents[1] / 'hermes-plugin/jarvis-device-access/__init__.py'


PLUGIN = _get_plugin_path()


def test_settings_must_be_private(tmp_path):
    path = tmp_path / 'settings.json'
    path.write_text('{}')
    path.chmod(0o644)
    with pytest.raises(ValueError):
        read_settings(path)
    path.chmod(0o600)
    assert read_settings(path) == {}
    link = tmp_path / 'link'
    link.symlink_to(path)
    with pytest.raises(OSError):
        read_settings(link)


def test_send_delivers_copyable_token_and_does_not_return_token(tmp_path, monkeypatch):
    env = tmp_path / '.env'
    env.write_text('TELEGRAM_BOT_TOKEN=test-secret\n')
    captured = {}
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'{"ok":true,"result":{"message_id":123}}'
    def fake_send(request, timeout):
        captured.update(json.loads(request.data))
        return Response()
    monkeypatch.setattr('nexo_server.telegram_command.urlopen', fake_send)
    monkeypatch.setattr('jarvis_server.telegram_command.urlopen', fake_send)
    payload = json.dumps({'device_id': 'tablet_01', 'token': 'test_tok_123', 'origin': 'wss://example.com'})
    assert send_protected(payload, {'telegram_env_file': str(env), 'owner_chat_id': '123'}) is None
    # Must NOT set protect_content so user can select and copy text
    assert captured.get('protect_content') is not True
    assert captured['chat_id'] == '123'
    assert 'test_tok_123' in captured['text']
    assert captured['parse_mode'] == 'HTML'
    assert captured['reply_markup']['inline_keyboard'][0][0]['copy_text']['text'] == 'test_tok_123'


def test_telegram_exception_is_sanitized(tmp_path, monkeypatch):
    env = tmp_path / '.env'
    env.write_text('TELEGRAM_BOT_TOKEN=test-secret\n')
    def fail(*args, **kwargs): raise RuntimeError('test-secret')
    monkeypatch.setattr('nexo_server.telegram_command.urlopen', fail)
    monkeypatch.setattr('jarvis_server.telegram_command.urlopen', fail)
    with pytest.raises(ValueError, match='Private Telegram delivery failed') as exc:
        send_protected('synthetic token', {'telegram_env_file': str(env), 'owner_chat_id': '123'})
    assert 'test-secret' not in str(exc.value)


def test_plugin_rejects_nontelegram_before_worker(monkeypatch):
    spec = importlib.util.spec_from_file_location('device_access_plugin', PLUGIN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    def forbidden(*args, **kwargs): raise AssertionError('Worker must not run')
    monkeypatch.setattr(module.subprocess, 'run', forbidden)
    assert 'privat' in module._handle('tablet', source=None, platform='telegram')
    assert 'privat' in module._handle('tablet', source=SimpleNamespace(), platform='local')


def test_telegram_command_dual_compatibility():
    """Verify legacy jarvis_server telegram command functions match nexo_server."""
    import jarvis_server.telegram_command as legacy_cmd
    assert callable(legacy_cmd.read_settings)
    assert callable(legacy_cmd.send_protected)
