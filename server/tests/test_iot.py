"""Tests for Nexo Smart Appliance IoT control and server telemetry."""

from fastapi.testclient import TestClient
import pytest

from nexo_server.iot import (
    get_system_telemetry,
    format_system_telemetry_speech,
    get_iot_state,
    update_iot_state,
    parse_iot_command,
)
from nexo_server.main import app
from nexo_server.mascot import MascotExpression


def test_system_telemetry_fields():
    data = get_system_telemetry()
    assert "cpu_load_1m" in data
    assert "ram_total_mb" in data
    assert "ram_available_mb" in data
    assert "ram_used_percent" in data
    assert "uptime_seconds" in data
    assert "disk_free_gb" in data
    assert data["cpu_load_1m"] >= 0.0
    assert data["ram_total_mb"] >= 0


def test_format_system_telemetry_speech():
    dummy = {
        "cpu_load_1m": 0.25,
        "ram_used_percent": 45.0,
        "disk_free_gb": 12.5,
        "uptime_seconds": 90000,
        "temperature_c": 38.5,
    }
    speech = format_system_telemetry_speech(dummy)
    assert "Status server saat ini beroperasi prima" in speech
    assert "Beban CPU 0 koma 25" in speech
    assert "45 persen" in speech
    assert "12 koma 5 gigabita" in speech
    assert "38 koma 5 derajat Celcius" in speech
    assert "Pak Yanto" in speech


def test_iot_state_management():
    update_iot_state(scene="normal", device="studio_light", state="off")
    state = get_iot_state()
    assert state["active_scene"] == "normal"
    assert state["devices"]["studio_light"] == "off"

    new_state = update_iot_state(scene="focus", device="studio_light", state="on")
    assert new_state["active_scene"] == "focus"
    assert new_state["devices"]["studio_light"] == "on"


@pytest.mark.parametrize("cmd", [
    "status server",
    "cek status server",
    "kondisi server",
    "berapa suhu server saat ini",
    "cek ram",
    "penggunaan memori",
])
def test_parse_server_telemetry_commands(cmd):
    res = parse_iot_command(cmd, is_sleeping=False)
    assert res is not None
    reply, expr, event = res
    assert "Status server saat ini" in reply
    assert expr == MascotExpression.KEREN
    assert event["type"] == "iot_telemetry"
    assert "cpu_load_1m" in event["metrics"]


def test_parse_focus_and_relax_commands():
    # Focus
    res_focus = parse_iot_command("nexo tolong aktifkan mode fokus", is_sleeping=False)
    assert res_focus is not None
    reply, expr, event = res_focus
    assert "Mode fokus diaktifkan" in reply
    assert expr == MascotExpression.KEREN
    assert event["type"] == "iot_control"
    assert event["action"] == "focus_mode"

    # Relax
    res_relax = parse_iot_command("mode santai dong", is_sleeping=False)
    assert res_relax is not None
    reply, expr, event = res_relax
    assert "Mode santai diaktifkan" in reply
    assert expr == MascotExpression.SENANG
    assert event["type"] == "iot_control"
    assert event["action"] == "relax_mode"


def test_parse_light_and_combo_commands():
    # Light on
    res_on = parse_iot_command("nyalakan lampu studio", is_sleeping=False)
    assert res_on is not None
    reply, expr, event = res_on
    assert "Lampu studio telah dinyalakan" in reply
    assert expr == MascotExpression.SENANG

    # Light off
    res_off = parse_iot_command("matikan lampu", is_sleeping=False)
    assert res_off is not None
    reply, expr, event = res_off
    assert "Lampu studio telah dimatikan" in reply
    assert expr == MascotExpression.NETRAL

    # Combo
    res_combo = parse_iot_command("nyalakan lampu studio dan pasang mode fokus", is_sleeping=False)
    assert res_combo is not None
    reply, expr, event = res_combo
    assert "Lampu studio dinyalakan dan mode fokus diaktifkan" in reply
    assert expr == MascotExpression.SANGAT_SENANG


def test_iot_sleeping_ignores_commands():
    assert parse_iot_command("status server", is_sleeping=True) is None
    assert parse_iot_command("mode fokus", is_sleeping=True) is None


def test_iot_endpoints_security():
    client = TestClient(app)
    # External request (Cloudflare headers) -> must be blocked
    headers = {"cf-connecting-ip": "203.0.113.10", "cf-ray": "12345678-SIN"}
    assert client.get("/iot/status", headers=headers).status_code == 403
    assert client.post("/iot/control", json={"action": "focus_mode"}, headers=headers).status_code == 403

    # Local request -> allowed
    resp_status = client.get("/iot/status")
    assert resp_status.status_code == 200
    json_status = resp_status.json()
    assert json_status["status"] == "healthy"
    assert "telemetry" in json_status
    assert "iot_state" in json_status

    resp_ctrl = client.post("/iot/control", json={"action": "focus_mode"})
    assert resp_ctrl.status_code == 200
    json_ctrl = resp_ctrl.json()
    assert json_ctrl["status"] == "ok"
    assert json_ctrl["action"] == "focus_mode"
    assert json_ctrl["iot_state"]["active_scene"] == "focus"
