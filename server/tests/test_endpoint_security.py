"""Security tests for local-only operator endpoint enforcement."""

from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient
import pytest

from nexo_server.main import app
from nexo_server.session_hub import hub


def test_public_health_accessible_externally():
    client = TestClient(app)
    # Cloudflare headers attached by external proxy
    headers = {
        "cf-connecting-ip": "203.0.113.5",
        "cf-ray": "8e123456789abcde-SIN",
    }
    resp = client.get("/health", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


def test_session_send_blocked_when_external():
    client = TestClient(app)
    headers = {
        "cf-connecting-ip": "203.0.113.5",
        "cf-ray": "8e123456789abcde-SIN",
    }
    resp = client.post("/session/send", json={"message": "pwned"}, headers=headers)
    assert resp.status_code == 403
    assert "restricted to local terminal CLI" in resp.json()["detail"]


def test_session_transcript_blocked_when_external():
    client = TestClient(app)
    headers = {
        "cf-connecting-ip": "203.0.113.5",
    }
    resp = client.get("/session/transcript", headers=headers)
    assert resp.status_code == 403
    assert "restricted to local terminal CLI" in resp.json()["detail"]


def test_session_speak_blocked_when_external():
    client = TestClient(app)
    headers = {
        "x-forwarded-for": "198.51.100.22",
    }
    resp = client.post("/session/speak", json={"text": "speak"}, headers=headers)
    assert resp.status_code == 403
    assert "restricted to local terminal CLI" in resp.json()["detail"]


def test_session_endpoints_allowed_locally():
    mock_bridge = MagicMock()
    mock_bridge.query = AsyncMock(return_value="Koneksi lokal aman.")
    mock_bridge.session_name = "nexo"
    hub.set_hermes_bridge(mock_bridge)

    client = TestClient(app)
    # No external headers -> local request
    resp = client.post("/session/send", json={"message": "tes local"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
