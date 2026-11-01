"""Probe credentials cannot be silently sent to another origin."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

spec = importlib.util.spec_from_file_location("probe_voice", Path(__file__).resolve().parents[2] / "scripts/probe_voice.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_probe_never_follows_redirect():
    # Both current and legacy websockets hook names are fenced.
    connector = probe.NoRedirectConnect('wss://voice.example/ws/appliance')
    error = ValueError('redirect')
    assert connector.process_redirect(error) is error
    with pytest.raises(ValueError, match='Redirects are disabled'):
        connector.handle_redirect('wss://evil.example/ws/appliance')


def test_probe_reads_private_origin_bound_credential(tmp_path):
    file = tmp_path / 'issued.json'
    file.write_text(json.dumps({'device_id': 'tablet_01', 'token': 'a' * 43, 'origin': 'wss://voice.example:443'}))
    file.chmod(0o600)
    args = SimpleNamespace(credentials=str(file), url='wss://voice.example/ws/appliance')
    headers, device = probe.auth_headers(args)
    assert device == 'tablet_01'
    assert headers['Authorization'] == 'Bearer ' + 'a' * 43
    for url in ['wss://evil.example/ws/appliance', 'wss://voice.example:444/ws/appliance',
                'wss://voice.example:0/ws/appliance', 'ws://voice.example/ws/appliance',
                'wss://voice.example/ws/appliance?token=x']:
        args.url = url
        with pytest.raises(ValueError):
            probe.auth_headers(args)
    args.url = 'wss://voice.example/ws/appliance'
    file.chmod(0o644)
    with pytest.raises(ValueError):
        probe.auth_headers(args)
