"""Smart Appliance IoT Control, Studio Scenes, and Real-Time Server Telemetry."""

import asyncio
import json
import logging
import os
from pathlib import Path
import re
import shutil
import time
import urllib.request
from typing import Any, Dict, Optional, Tuple

from .mascot import MascotExpression, _COMMAND

logger = logging.getLogger(__name__)

# In-memory state of smart studio devices and active scene
_IOT_STATE: Dict[str, Any] = {
    "active_scene": "normal",
    "devices": {
        "studio_light": "off",
        "ambient_backlight": "on",
    },
    "last_updated": 0,
}

_SERVER_STATUS_COMMANDS = {
    "cek status server",
    "status server",
    "kondisi server",
    "info server",
    "cek server",
    "status sistem",
    "cek sistem",
    "kesehatan server",
    "berapa suhu server",
    "berapa suhu server saat ini",
    "suhu server",
    "suhu server saat ini",
    "cek memori",
    "penggunaan memori",
    "berapa penggunaan memori",
    "cek ram",
    "penggunaan ram",
    "cek cpu",
    "beban cpu",
    "uptime server",
}

_FOCUS_SCENE_COMMANDS = {
    "mode fokus",
    "aktifkan mode fokus",
    "pasang mode fokus",
    "nyalakan mode fokus",
    "masuk mode fokus",
    "fokus kerja",
}

_RELAX_SCENE_COMMANDS = {
    "mode santai",
    "aktifkan mode santai",
    "pasang mode santai",
    "nyalakan mode santai",
    "mode istirahat",
    "aktifkan mode istirahat",
    "suasana santai",
}

_LIGHT_ON_COMMANDS = {
    "nyalakan lampu",
    "nyalakan lampu studio",
    "hidupkan lampu",
    "hidupkan lampu studio",
    "nyalakan lampu kerja",
    "hidupkan lampu kerja",
}

_LIGHT_OFF_COMMANDS = {
    "matikan lampu",
    "matikan lampu studio",
    "padamkan lampu",
    "padamkan lampu studio",
    "matikan lampu kerja",
}

_COMBO_FOCUS_LIGHT_COMMANDS = {
    "nyalakan lampu studio dan pasang mode fokus",
    "nyalakan lampu dan pasang mode fokus",
    "nyalakan lampu dan aktifkan mode fokus",
    "pasang mode fokus dan nyalakan lampu studio",
    "pasang mode fokus dan nyalakan lampu",
}


def get_iot_state() -> Dict[str, Any]:
    """Return a snapshot of the current smart studio state."""
    return {
        "active_scene": _IOT_STATE["active_scene"],
        "devices": dict(_IOT_STATE["devices"]),
        "last_updated": _IOT_STATE["last_updated"],
    }


def update_iot_state(
    scene: Optional[str] = None,
    device: Optional[str] = None,
    state: Optional[str] = None,
) -> Dict[str, Any]:
    """Update smart studio state and return the updated snapshot."""
    if scene is not None:
        _IOT_STATE["active_scene"] = scene
    if device is not None and state is not None:
        _IOT_STATE["devices"][device] = state
    _IOT_STATE["last_updated"] = int(time.time() * 1000)
    return get_iot_state()


def get_system_telemetry() -> Dict[str, Any]:
    """Read real Linux server telemetry (CPU load, RAM, Disk, Uptime, Thermal)."""
    load_1m, load_5m, load_15m = 0.0, 0.0, 0.0
    try:
        load_1m, load_5m, load_15m = os.getloadavg()
    except OSError:
        pass

    mem_total_mb = 0
    mem_avail_mb = 0
    mem_used_pct = 0.0
    try:
        meminfo = Path("/proc/meminfo").read_text(encoding="utf-8")
        total_kb = 0
        avail_kb = 0
        for line in meminfo.splitlines():
            if line.startswith("MemTotal:"):
                total_kb = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                avail_kb = int(line.split()[1])
        if total_kb > 0:
            mem_total_mb = round(total_kb / 1024)
            mem_avail_mb = round(avail_kb / 1024)
            mem_used_pct = round((total_kb - avail_kb) / total_kb * 100, 1)
    except OSError:
        pass

    uptime_seconds = 0.0
    try:
        raw_uptime = Path("/proc/uptime").read_text(encoding="utf-8").split()[0]
        uptime_seconds = float(raw_uptime)
    except (OSError, ValueError, IndexError):
        pass

    disk_free_gb = 0.0
    disk_total_gb = 0.0
    try:
        usage = shutil.disk_usage("/")
        disk_free_gb = round(usage.free / (1024 ** 3), 1)
        disk_total_gb = round(usage.total / (1024 ** 3), 1)
    except OSError:
        pass

    temp_c: Optional[float] = None
    try:
        for zone in sorted(Path("/sys/class/thermal").glob("thermal_zone*/temp")):
            raw_temp = int(zone.read_text(encoding="utf-8").strip())
            if raw_temp > 0:
                temp_c = round(raw_temp / 1000.0, 1)
                break
    except (OSError, ValueError):
        pass

    return {
        "cpu_load_1m": round(load_1m, 2),
        "cpu_load_5m": round(load_5m, 2),
        "cpu_load_15m": round(load_15m, 2),
        "ram_total_mb": mem_total_mb,
        "ram_available_mb": mem_avail_mb,
        "ram_used_percent": mem_used_pct,
        "disk_free_gb": disk_free_gb,
        "disk_total_gb": disk_total_gb,
        "uptime_seconds": round(uptime_seconds),
        "uptime_days": round(uptime_seconds / 86400, 1),
        "temperature_c": temp_c,
    }


def format_system_telemetry_speech(metrics: Optional[Dict[str, Any]] = None) -> str:
    """Format system telemetry into natural, concise Indonesian speech for TTS."""
    data = metrics or get_system_telemetry()
    load_str = str(data["cpu_load_1m"]).replace(".", " koma ")
    ram_pct = round(data["ram_used_percent"])
    disk_free = str(data["disk_free_gb"]).replace(".", " koma ")

    uptime_sec = data.get("uptime_seconds", 0)
    days = uptime_sec // 86400
    hours = (uptime_sec % 86400) // 3600
    if days > 0:
        uptime_str = f"{days} hari {hours} jam" if hours > 0 else f"{days} hari"
    elif hours > 0:
        uptime_str = f"{hours} jam"
    else:
        mins = max(1, uptime_sec // 60)
        uptime_str = f"{mins} menit"

    temp_part = ""
    if data.get("temperature_c") is not None:
        temp_str = str(data["temperature_c"]).replace(".", " koma ")
        temp_part = f" Suhu prosesor {temp_str} derajat Celcius."

    return (
        f"Status server saat ini beroperasi prima.{temp_part} "
        f"Beban CPU {load_str}, pemakaian memori RAM {ram_pct} persen, "
        f"ruang penyimpanan bebas {disk_free} gigabita, "
        f"dan sistem telah aktif selama {uptime_str}. "
        "Seluruh layanan Nexo siap mendampingi Anda, Pak Yanto."
    )


async def trigger_external_iot_webhook(event_payload: Dict[str, Any]) -> bool:
    """Optionally forward IoT state changes to a configured Home Assistant / webhook URL."""
    webhook_url = os.getenv("NEXO_IOT_WEBHOOK_URL", "").strip()
    if not webhook_url or not webhook_url.startswith(("http://", "https://")):
        return False

    def _post():
        body = json.dumps(event_payload).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            return 200 <= resp.status < 300

    try:
        return await asyncio.to_thread(_post)
    except Exception as exc:
        logger.warning("External IoT webhook delivery failed: %s", exc)
        return False


def parse_iot_command(
    text: str, is_sleeping: bool = False
) -> Optional[Tuple[str, MascotExpression, Dict[str, Any]]]:
    """Parse standalone smart home, studio mode, and server telemetry voice commands.

    Returns:
        (reply_text, expression, iot_event_dict) or None if not a recognized IoT command.
    """
    if not text or is_sleeping:
        return None

    clean = " ".join(text.casefold().strip().split())
    clean = re.sub(r"[.?!,]+$", "", clean).strip()
    clean_body = clean
    match = _COMMAND.fullmatch(clean)
    if match:
        clean_body = re.sub(r"[.?!,]+$", "", match["body"].strip()).strip()

    # 1. Combo: Studio Light + Focus Mode
    if clean in _COMBO_FOCUS_LIGHT_COMMANDS or clean_body in _COMBO_FOCUS_LIGHT_COMMANDS:
        state_snap = update_iot_state(scene="focus", device="studio_light", state="on")
        event = {
            "type": "iot_control",
            "action": "studio_focus_combo",
            "iot_state": state_snap,
        }
        reply = (
            "Lampu studio dinyalakan dan mode fokus diaktifkan. "
            "Seluruh instrumen kerja siap mendampingi Anda berkarya dengan hati, Pak Yanto!"
        )
        return reply, MascotExpression.SANGAT_SENANG, event

    # 2. Focus Mode
    if clean in _FOCUS_SCENE_COMMANDS or clean_body in _FOCUS_SCENE_COMMANDS:
        state_snap = update_iot_state(scene="focus", device="ambient_backlight", state="focus_cyan")
        event = {
            "type": "iot_control",
            "action": "focus_mode",
            "iot_state": state_snap,
        }
        reply = (
            "Mode fokus diaktifkan. Suasana studio disiapkan untuk konsentrasi penuh. "
            "Selamat berkarya dengan hati, Pak Yanto!"
        )
        return reply, MascotExpression.KEREN, event

    # 3. Relax Mode
    if clean in _RELAX_SCENE_COMMANDS or clean_body in _RELAX_SCENE_COMMANDS:
        state_snap = update_iot_state(scene="relax", device="ambient_backlight", state="warm_amber")
        event = {
            "type": "iot_control",
            "action": "relax_mode",
            "iot_state": state_snap,
        }
        reply = (
            "Mode santai diaktifkan. Suasana ruangan dibuat lebih tenang dan hangat. "
            "Selamat beristirahat sejenak, Pak Yanto."
        )
        return reply, MascotExpression.SENANG, event

    # 4. Studio Light ON
    if clean in _LIGHT_ON_COMMANDS or clean_body in _LIGHT_ON_COMMANDS:
        state_snap = update_iot_state(device="studio_light", state="on")
        event = {
            "type": "iot_control",
            "action": "studio_light_on",
            "iot_state": state_snap,
        }
        reply = "Lampu studio telah dinyalakan. Ruangan kerja siap digunakan, Pak Yanto."
        return reply, MascotExpression.SENANG, event

    # 5. Studio Light OFF
    if clean in _LIGHT_OFF_COMMANDS or clean_body in _LIGHT_OFF_COMMANDS:
        state_snap = update_iot_state(device="studio_light", state="off")
        event = {
            "type": "iot_control",
            "action": "studio_light_off",
            "iot_state": state_snap,
        }
        reply = "Lampu studio telah dimatikan."
        return reply, MascotExpression.NETRAL, event

    # 6. Server Telemetry & Health Check
    if clean in _SERVER_STATUS_COMMANDS or clean_body in _SERVER_STATUS_COMMANDS:
        metrics = get_system_telemetry()
        event = {
            "type": "iot_telemetry",
            "action": "server_status",
            "metrics": metrics,
        }
        reply = format_system_telemetry_speech(metrics)
        return reply, MascotExpression.KEREN, event

    return None
