"""Session routing refuses cross-session requests at both control surfaces."""
from fastapi.testclient import TestClient

from nexo_server.main import app
from nexo_server.session_hub import hub


def test_http_link_rejects_switch_without_mutation():
    client = TestClient(app)
    response = client.post('/session/link', json={'session_name': 'unrelated'})
    assert response.status_code == 409
    assert hub.current_session_thread == 'nexo'


def test_http_send_rejects_foreign_session_without_query():
    client = TestClient(app)
    response = client.post('/session/send', json={
        'message': 'Do not query', 'session_name': 'unrelated',
    })
    assert response.status_code == 409


def test_websocket_rejects_switch_and_keeps_connection():
    client = TestClient(app)
    with client.websocket_connect('/ws/session/stream') as ws:
        assert ws.receive_json()['type'] == 'status'
        ws.send_json({'type': 'link', 'session_name': 'unrelated'})
        result = ws.receive_json()
        assert result['type'] == 'error'
        assert result['code'] == 'session_locked'
        ws.send_json({'type': 'ping', 'timestamp': 12})
        assert ws.receive_json() == {'type': 'pong', 'timestamp': 12}
