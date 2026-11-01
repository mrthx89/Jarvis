"""Telegram owner provisioning tests: temporary credentials, never live sends."""
import importlib
import importlib.util
import json
from pathlib import Path
import stat

import pytest

from nexo_server.auth import DeviceRegistry


@pytest.fixture
def config(tmp_path):
    delivery = tmp_path / 'delivery'
    delivery.mkdir(mode=0o700)
    tmp_path.chmod(0o700)
    return {'registry_path': str(tmp_path / 'registry.json'),
            'delivery_dir': str(delivery), 'origin': 'wss://voice.example/ws/appliance',
            'owner_user_id': '123', 'owner_chat_id': '456'}


@pytest.fixture
def owner():
    return {'platform': 'telegram', 'chat_type': 'dm', 'is_bot': False,
            'user_id': '123', 'chat_id': '456'}


def handle(*args):
    assert importlib.util.find_spec('nexo_server.telegram_provision') is not None, 'Provisioning API is missing'
    return importlib.import_module('nexo_server.telegram_provision').handle_command(*args)


@pytest.mark.parametrize('changes', [
    {'platform': 'discord'}, {'chat_type': 'group'}, {'chat_type': 'channel'},
    {'is_bot': True}, {'is_bot': None}, {'user_id': 'wrong'}, {'chat_id': 'wrong'},
    {'user_id': 123}, {'chat_id': 456},
])
def test_unauthorized_rejected_before_io(config, owner, changes, monkeypatch):
    import nexo_server.telegram_provision as provision
    owner.update(changes)
    def forbidden(*args, **kwargs):
        pytest.fail('Unauthorized request reached IO')
    monkeypatch.setattr(provision, '_registry_lock', forbidden)
    assert 'denied' in handle('tablet_01', owner, config, forbidden).lower()


@pytest.mark.parametrize('raw', ['', ' ', '../escape', 'bad/id', 'a' * 129,
                                  'rotate', 'deliver', 'revoke', 'rotate a extra',
                                  'create a', 'help extra', None, 123])
def test_invalid_args_have_no_io(config, owner, raw, monkeypatch):
    import nexo_server.telegram_provision as provision
    def forbidden(*args, **kwargs):
        pytest.fail('Invalid request reached IO')
    monkeypatch.setattr(provision, '_registry_lock', forbidden)
    assert 'usage' in handle(raw, owner, config, forbidden).lower()


def test_help_has_no_io(config, owner, monkeypatch):
    import nexo_server.telegram_provision as provision
    def forbidden(*args, **kwargs):
        pytest.fail('Help reached IO')
    monkeypatch.setattr(provision, '_registry_lock', forbidden)
    summary = handle('help', owner, config, forbidden)
    assert all(word in summary for word in ('DEVICE_ID', 'rotate', 'deliver', 'revoke'))


def test_create_private_delivery_and_hash_only_registry(config, owner, capsys):
    sent = []
    summary = handle('tablet_01', owner, config, sent.append)
    assert len(sent) == 1
    payload = json.loads(sent[0])
    assert payload['device_id'] == 'tablet_01'
    assert payload['origin'] == 'wss://voice.example:443'
    assert len(payload['token']) == 43
    registry = Path(config['registry_path'])
    delivery = Path(config['delivery_dir']) / 'tablet_01.json'
    assert json.loads(delivery.read_text()) == payload
    assert payload['token'] not in summary + registry.read_text() + str(capsys.readouterr())
    assert DeviceRegistry(registry).authenticate('tablet_01', 'Bearer ' + payload['token'])
    for path in (registry, delivery, registry.with_name(registry.name + '.lock')):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_duplicate_create_does_not_rotate_or_resend(config, owner):
    sent = []
    handle('tablet', owner, config, sent.append)
    registry = Path(config['registry_path'])
    delivery = Path(config['delivery_dir']) / 'tablet.json'
    before = registry.read_bytes(), delivery.read_bytes()
    summary = handle('tablet', owner, config, sent.append)
    assert 'exists' in summary.lower()
    assert 'rotate tablet' in summary and 'deliver tablet' in summary
    assert len(sent) == 1
    assert before == (registry.read_bytes(), delivery.read_bytes())


def test_rotate_invalidates_old_token_and_replaces_stable_delivery(config, owner):
    sent = []
    handle('tablet', owner, config, sent.append)
    old = json.loads(sent[-1])['token']
    summary = handle('rotate tablet', owner, config, sent.append)
    new = json.loads(sent[-1])['token']
    registry = DeviceRegistry(config['registry_path'])
    assert new != old and len(sent) == 2
    assert not registry.authenticate('tablet', 'Bearer ' + old)
    assert registry.authenticate('tablet', 'Bearer ' + new)
    assert list(Path(config['delivery_dir']).iterdir()) == [Path(config['delivery_dir']) / 'tablet.json']
    assert json.loads((Path(config['delivery_dir']) / 'tablet.json').read_text())['token'] == new
    assert old not in summary and new not in summary


def test_deliver_resends_current_without_mutation(config, owner):
    sent = []
    handle('tablet', owner, config, sent.append)
    before = Path(config['registry_path']).read_bytes()
    summary = handle('deliver tablet', owner, config, sent.append)
    assert len(sent) == 2 and json.loads(sent[0]) == json.loads(sent[1])
    assert before == Path(config['registry_path']).read_bytes()
    assert json.loads(sent[0])['token'] not in summary


def test_revoke_removes_auth_and_delivery_and_never_sends(config, owner):
    sent = []
    handle('tablet', owner, config, sent.append)
    token = json.loads(sent[0])['token']
    summary = handle('revoke tablet', owner, config, sent.append)
    assert 'revoked' in summary.lower() and token not in summary
    assert len(sent) == 1
    assert not DeviceRegistry(config['registry_path']).authenticate('tablet', 'Bearer ' + token)
    assert not (Path(config['delivery_dir']) / 'tablet.json').exists()
    handle('deliver tablet', owner, config, sent.append)
    assert len(sent) == 1


@pytest.mark.parametrize('result', ['raise', 'false'])
def test_sender_failure_preserves_issuance_and_gives_retry(config, owner, result, capsys):
    captured = []
    def fail(text):
        captured.append(json.loads(text))
        if result == 'raise':
            raise RuntimeError(text)
        return False
    summary = handle('tablet', owner, config, fail)
    token = captured[0]['token']
    assert 'deliver tablet' in summary
    assert token not in summary + str(capsys.readouterr())
    assert DeviceRegistry(config['registry_path']).authenticate('tablet', 'Bearer ' + token)
    sent = []
    handle('deliver tablet', owner, config, sent.append)
    assert json.loads(sent[0]) == captured[0]


def test_deliver_hash_mismatch_never_sends(config, owner):
    sent = []
    handle('tablet', owner, config, sent.append)
    path = Path(config['delivery_dir']) / 'tablet.json'
    payload = json.loads(path.read_text())
    payload['token'] = 'x' * 43
    path.write_text(json.dumps(payload))
    before = Path(config['registry_path']).read_bytes()
    summary = handle('deliver tablet', owner, config, sent.append)
    assert len(sent) == 1 and payload['token'] not in summary
    assert 'failed' in summary.lower()
    assert Path(config['registry_path']).read_bytes() == before


def test_max_devices_limits_create_but_not_rotate_or_revoke(config, owner):
    config['max_devices'] = 1
    sent = []
    handle('one', owner, config, sent.append)
    before = Path(config['registry_path']).read_bytes()
    summary = handle('two', owner, config, sent.append)
    assert 'limit' in summary.lower() and len(sent) == 1
    assert Path(config['registry_path']).read_bytes() == before
    handle('rotate one', owner, config, sent.append)
    assert len(sent) == 2
    handle('revoke one', owner, config, sent.append)
    handle('two', owner, config, sent.append)
    assert len(sent) == 3


@pytest.mark.parametrize('limit', [True, 0, -1, '1', None])
def test_invalid_device_limit_fails_without_issuance(config, owner, limit):
    config['max_devices'] = limit
    sent = []
    assert 'failed' in handle('one', owner, config, sent.append).lower()
    assert not sent and not Path(config['registry_path']).exists()


@pytest.mark.parametrize('directory', ['delivery_dir', 'registry_path'])
def test_insecure_parent_fails_without_issuance(config, owner, directory):
    parent = Path(config[directory])
    if directory == 'registry_path':
        parent = parent.parent
    parent.chmod(0o755)
    sent = []
    assert 'failed' in handle('one', owner, config, sent.append).lower()
    assert not sent and not Path(config['registry_path']).exists()


@pytest.mark.parametrize('damage', ['symlink', 'public', 'missing', 'invalid', 'large', 'wrong_device', 'bad_origin'])
def test_deliver_rejects_unsafe_or_invalid_file(config, owner, damage):
    sent = []
    handle('tablet', owner, config, sent.append)
    path = Path(config['delivery_dir']) / 'tablet.json'
    payload = json.loads(path.read_text())
    if damage == 'symlink':
        target = path.with_name('target.json')
        path.rename(target)
        path.symlink_to(target)
    elif damage == 'public':
        path.chmod(0o644)
    elif damage == 'missing':
        path.unlink()
    elif damage == 'invalid':
        path.write_text('not JSON')
    elif damage == 'large':
        path.write_text(' ' * 4097)
    else:
        payload['device_id' if damage == 'wrong_device' else 'origin'] = 'wrong'
        path.write_text(json.dumps(payload))
    before = Path(config['registry_path']).read_bytes()
    assert 'failed' in handle('deliver tablet', owner, config, sent.append).lower()
    assert len(sent) == 1
    assert Path(config['registry_path']).read_bytes() == before


@pytest.mark.parametrize('damage', ['symlink', 'dangling_symlink', 'public', 'invalid'])
def test_registry_damage_never_overwritten(config, owner, damage):
    path = Path(config['registry_path'])
    if damage in {'symlink', 'dangling_symlink'}:
        target = path.with_name('target.json')
        if damage == 'symlink':
            target.write_text('{"version":1,"devices":{}}')
            target.chmod(0o600)
        path.symlink_to(target)
    else:
        path.write_text('bad' if damage == 'invalid' else '{"version":1,"devices":{}}')
        path.chmod(0o644 if damage == 'public' else 0o600)
    sent = []
    assert 'failed' in handle('tablet', owner, config, sent.append).lower()
    assert not sent
    if 'symlink' in damage:
        assert path.is_symlink()
    else:
        assert path.read_text() == ('bad' if damage == 'invalid' else '{"version":1,"devices":{}}')


@pytest.mark.parametrize('command', ['rotate absent', 'deliver absent', 'revoke absent'])
def test_unknown_device_never_sends_or_creates_credentials(config, owner, command):
    sent = []
    handle(command, owner, config, sent.append)
    assert not sent and not list(Path(config['delivery_dir']).iterdir())
    if Path(config['registry_path']).exists():
        assert DeviceRegistry(config['registry_path']).read()['devices'] == {}


def test_parallel_creates_preserve_all_cooperating_writers(config, owner):
    from concurrent.futures import ThreadPoolExecutor
    sent = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        summaries = list(pool.map(lambda number: handle(f'device_{number}', owner, config, sent.append), range(16)))
    assert all('sent privately' in summary for summary in summaries)
    assert len(sent) == 16
    registry = DeviceRegistry(config['registry_path'])
    assert len(registry.read()['devices']) == 16
    for text in sent:
        payload = json.loads(text)
        assert registry.authenticate(payload['device_id'], 'Bearer ' + payload['token'])


def test_cli_writer_and_telegram_share_lock_and_registry(config, owner):
    import os
    import subprocess
    import sys
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(handle, 'telegram_device', owner, config, lambda text: None)
        result = subprocess.run([sys.executable, '-m', 'nexo_server.auth', 'provision',
                                 '--registry', config['registry_path'], '--device-id', 'cli_device',
                                 '--output', str(Path(config['delivery_dir']) / 'cli_device.json'),
                                 '--origin', config['origin']], capture_output=True, text=True, env=os.environ.copy())
        assert 'sent privately' in future.result()
    assert result.returncode == 0
    assert set(DeviceRegistry(config['registry_path']).read()['devices']) == {'telegram_device', 'cli_device'}


def test_telegram_provision_dual_compatibility():
    """Verify legacy nexo_server telegram provision imports match nexo_server."""
    import nexo_server.telegram_provision as legacy_provision
    assert callable(legacy_provision.handle_command)

