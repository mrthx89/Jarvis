"""Stage 1 owner provisioning; no network, models or live credentials."""
import hashlib
import json
import os
import subprocess
import sys


def test_owner_provisions_hash_only_and_revokes(tmp_path):
    registry = tmp_path / 'devices.json'
    issued = tmp_path / 'issued.json'
    result = subprocess.run([sys.executable, '-m', 'jarvis_server.auth', 'provision',
                             '--registry', str(registry), '--device-id', 'tablet_01',
                             '--output', str(issued), '--origin', 'wss://voice.example/ws/appliance'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    credential = json.loads(issued.read_text())
    token = credential['token']
    assert credential['device_id'] == 'tablet_01'
    assert credential['origin'] == 'wss://voice.example:443'
    assert len(token) >= 43
    assert token not in registry.read_text()
    assert token not in result.stdout + result.stderr
    assert oct(os.stat(registry).st_mode & 0o777) == '0o600'
    assert oct(os.stat(issued).st_mode & 0o777) == '0o600'
    from jarvis_server.auth import DeviceRegistry
    assert DeviceRegistry(registry).authenticate('tablet_01', 'Bearer ' + token)
    assert not DeviceRegistry(registry).authenticate('tablet_02', 'Bearer ' + token)
    assert not DeviceRegistry(registry).authenticate('tablet_01', 'Bearer wrong')
    revoked = subprocess.run([sys.executable, '-m', 'jarvis_server.auth', 'revoke',
                              '--registry', str(registry), '--device-id', 'tablet_01'],
                             capture_output=True, text=True)
    assert revoked.returncode == 0, revoked.stderr
    assert not DeviceRegistry(registry).authenticate('tablet_01', 'Bearer ' + token)


def test_registry_missing_invalid_or_unreadable_fails_closed(tmp_path):
    from jarvis_server.auth import DeviceRegistry
    path = tmp_path / 'devices.json'
    for content in (None, 'invalid', '[]', '{"version":1,"devices":{"tablet_01":{"token":"raw"}}}'):
        if content is not None:
            path.write_text(content)
            path.chmod(0o600)
        assert not DeviceRegistry(path).authenticate('tablet_01', 'Bearer ' + 'x' * 43)
    assert not DeviceRegistry('').authenticate('tablet_01', 'Bearer ' + 'x' * 43)


def test_registry_permissions_and_header_shape(tmp_path):
    from jarvis_server.auth import DeviceRegistry
    path = tmp_path / 'devices.json'
    token = 'a' * 43
    path.write_text(json.dumps({'version': 1, 'devices': {
        'tablet_01': {'token_sha256': hashlib.sha256(token.encode()).hexdigest()}}}))
    path.chmod(0o600)
    registry = DeviceRegistry(path)
    assert registry.authenticate('tablet_01', 'Bearer ' + token)
    for device, header in [('', 'Bearer ' + token), ('tablet_01', ''),
                            ('tablet_01', token), ('tablet_01', 'Bearer ' + token + ' '),
                            ('bad/id', 'Bearer ' + token)]:
        assert not registry.authenticate(device, header)
    path.chmod(0o644)
    assert not registry.authenticate('tablet_01', 'Bearer ' + token)

def test_provision_rejects_reuse_without_changing_registry(tmp_path):
    registry, output = tmp_path / 'devices.json', tmp_path / 'delivery.json'
    command = [sys.executable, '-m', 'jarvis_server.auth', 'provision', '--registry', str(registry),
               '--device-id', 'tablet_01', '--origin', 'wss://voice.example/ws/appliance',
               '--output', str(output)]
    assert subprocess.run(command, capture_output=True).returncode == 0
    before = registry.read_bytes()
    assert subprocess.run(command, capture_output=True).returncode != 0
    assert registry.read_bytes() == before


def test_deeply_nested_registry_fails_closed(tmp_path):
    from jarvis_server.auth import DeviceRegistry
    path = tmp_path / 'devices.json'
    path.write_text('[' * 2000 + ']' * 2000)
    path.chmod(0o600)
    assert not DeviceRegistry(path).authenticate('tablet_01', 'Bearer ' + 'a' * 43)
