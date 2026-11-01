"""Exercise the ASGI endpoint directly: no socket, external services or builds."""
import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest
from starlette.datastructures import Headers

from jarvis_server import main
from jarvis_server.auth import DeviceRegistry
from jarvis_server.gateway.websocket_handler import ApplianceSession, ProtocolViolation

TOKEN = 'a' * 43
HELLO = {'type': 'client_hello', 'device_id': 'tablet_01', 'protocol_version': '1.0',
         'audio_spec': {'format': 'audio/pcm', 'sample_rate': 16000, 'channels': 1, 'bit_depth': 16}}


class Socket:
    def __init__(self, headers=None, frames=()):
        self.headers = Headers(headers or {})
        self.client = None
        self.accepted = False
        self.closed = None
        self.messages = []
        self.frames = iter(frames)
        self.scope = {'extensions': {'websocket.http.response': {}}}
        self.denied = None

    async def accept(self):
        self.accepted = True

    async def close(self, code=1000, reason=''):
        self.closed = code

    async def send_denial_response(self, response):
        self.denied = response.status_code

    async def receive(self):
        try:
            return next(self.frames)
        except StopIteration:
            await asyncio.Event().wait()

    async def send_text(self, data):
        self.messages.append(json.loads(data))


@pytest.fixture
def configured(tmp_path, monkeypatch):
    path = tmp_path / 'devices.json'
    path.write_text(json.dumps({'version': 1, 'devices': {'tablet_01': {
        'token_sha256': hashlib.sha256(TOKEN.encode()).hexdigest()}}}))
    path.chmod(0o600)
    monkeypatch.setattr(main.config.server, 'device_registry_path', str(path), raising=False)
    monkeypatch.setattr(main.config.server, 'max_sessions', 1, raising=False)
    monkeypatch.setattr(main.config.server, 'hello_timeout_seconds', 0.02, raising=False)
    monkeypatch.setattr(main.config.server, 'max_json_bytes', 4096, raising=False)
    return path


@pytest.mark.asyncio
@pytest.mark.parametrize('headers', [{}, {'X-Device-ID': 'tablet_01'},
    {'X-Device-ID': 'tablet_01', 'Authorization': 'Bearer wrong'},
    {'X-Device-ID': 'other', 'Authorization': 'Bearer ' + TOKEN}])
async def test_auth_denied_before_accept(configured, headers):
    ws = Socket(headers, [{'bytes': b'\x00\x20' * 4000}])
    await main.websocket_appliance_endpoint(ws)
    assert not ws.accepted
    assert ws.denied in (401, 403) or ws.closed == 1008
    assert not ws.messages


@pytest.mark.asyncio
async def test_missing_registry_denies_valid_token(configured):
    configured.unlink()
    ws = Socket({'X-Device-ID': 'tablet_01', 'Authorization': 'Bearer ' + TOKEN})
    await main.websocket_appliance_endpoint(ws)
    assert not ws.accepted


@pytest.mark.asyncio
async def test_hello_timeout_and_concurrency_limit_release(configured):
    headers = {'X-Device-ID': 'tablet_01', 'Authorization': 'Bearer ' + TOKEN}
    first = Socket(headers)
    task = asyncio.create_task(main.websocket_appliance_endpoint(first))
    await asyncio.sleep(0)
    second = Socket(headers)
    await main.websocket_appliance_endpoint(second)
    assert not second.accepted
    assert second.denied == 503 or second.closed == 1013
    await asyncio.wait_for(task, 0.2)
    assert first.closed == 1008
    third = Socket(headers, [{'type': 'websocket.disconnect'}])
    await main.websocket_appliance_endpoint(third)
    assert third.accepted


@pytest.mark.asyncio
async def test_no_audio_before_hello(configured):
    ws = Socket({'X-Device-ID': 'tablet_01', 'Authorization': 'Bearer ' + TOKEN},
                [{'bytes': b'\x00\x20' * 4000}])
    await asyncio.wait_for(main.websocket_appliance_endpoint(ws), 0.2)
    assert ws.accepted and ws.closed == 1008
    assert not ws.messages


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', [
    '[]', '{', 'x' * 4097, json.dumps({'type': 'voice_start'}),
    json.dumps({**HELLO, 'device_id': 'other'}),
    json.dumps({**HELLO, 'protocol_version': '2.0'}),
    json.dumps({**HELLO, 'audio_spec': {**HELLO['audio_spec'], 'sample_rate': 24000}}),
    json.dumps({**HELLO, 'audio_spec': {**HELLO['audio_spec'], 'channels': True}}),
    json.dumps({'type': 'client_hello', 'device_id': 'tablet_01'})])
async def test_invalid_handshake_closed(configured, payload):
    ws = Socket({'X-Device-ID': 'tablet_01', 'Authorization': 'Bearer ' + TOKEN}, [{'text': payload}])
    await asyncio.wait_for(main.websocket_appliance_endpoint(ws), 0.2)
    assert ws.closed == 1008
    assert not ws.messages


@pytest.mark.asyncio
async def test_valid_hello_then_ping(configured):
    ws = Socket({'X-Device-ID': 'tablet_01', 'Authorization': 'Bearer ' + TOKEN}, [
        {'text': json.dumps(HELLO)}, {'text': '{"type":"ping","timestamp":123}'},
        {'type': 'websocket.disconnect'}])
    await asyncio.wait_for(main.websocket_appliance_endpoint(ws), 0.2)
    assert [m['type'] for m in ws.messages] == ['server_hello', 'pong']


@pytest.mark.asyncio
async def test_revocation_on_next_frame_cancels_session(configured):
    class RevokingSocket(Socket):
        async def receive(self):
            message = await super().receive()
            if message.get('text') == '{"type":"ping","timestamp":123}':
                configured.write_text('{"version":1,"devices":{}}')
            return message
    ws = RevokingSocket({'X-Device-ID': 'tablet_01', 'Authorization': 'Bearer ' + TOKEN}, [
        {'text': json.dumps(HELLO)}, {'text': '{"type":"ping","timestamp":123}'}])
    await main.websocket_appliance_endpoint(ws)
    assert ws.closed == 1008
    assert [m['type'] for m in ws.messages] == ['server_hello']


@pytest.mark.asyncio
async def test_low_level_session_denies_unbound_identity():
    ws = Socket()
    session = ApplianceSession(ws, 'test', None, None, None)
    with pytest.raises(ProtocolViolation):
        await session.handle_binary_frame(b'\x00\x20' * 4000)
    assert not session.audio_buffer
    assert not session.is_recording
    assert not ws.messages
    await session.close()

@pytest.mark.asyncio
async def test_real_starlette_denial_without_extension():
    from starlette.websockets import WebSocket
    sent = []
    async def receive():
        return {'type': 'websocket.connect'}
    async def send(message):
        sent.append(message)
    ws = WebSocket({'type': 'websocket', 'path': '/ws/appliance', 'headers': []}, receive, send)
    await main._deny(ws, 403, 1008)
    assert sent == [{'type': 'websocket.close', 'code': 1008, 'reason': ''}]


@pytest.mark.asyncio
async def test_real_starlette_denial_with_old_method_missing():
    from starlette.websockets import WebSocket
    sent = []
    async def receive():
        return {'type': 'websocket.connect'}
    async def send(message):
        sent.append(message)
    ws = WebSocket({'type': 'websocket', 'path': '/ws/appliance', 'headers': [],
                    'extensions': {'websocket.http.response': {}}}, receive, send)
    ws.send_denial_response = None  # Older allowed Starlette API has no helper.
    await main._deny(ws, 403, 1008)
    assert sent == [{'type': 'websocket.close', 'code': 1008, 'reason': ''}]
