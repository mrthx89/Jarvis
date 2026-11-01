"""Configuration loader for Nexo Server."""

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any, Dict
import yaml


@dataclass
class ServerSettings:
    host: str = "127.0.0.1"
    port: int = 8765
    ws_path: str = "/ws/appliance"
    ping_interval: int = 15
    ping_timeout: int = 30
    device_registry_path: str = ""  # External mode-0600 JSON; empty denies all.
    max_sessions: int = 4
    hello_timeout_seconds: float = 5.0
    max_json_bytes: int = 4096


@dataclass
class STTSettings:
    provider: str = "faster-whisper"
    model: str = "base"
    language: str = "id"


@dataclass
class TTSSettings:
    provider: str = "edge-tts"
    voice: str = "id-ID-ArdiNeural"


@dataclass
class HermesSettings:
    bridge_mode: str = "cli"
    hermes_binary: str = "hermes"
    timeout_seconds: int = 60


@dataclass
class AppConfig:
    server: ServerSettings = field(default_factory=ServerSettings)
    stt: STTSettings = field(default_factory=STTSettings)
    tts: TTSSettings = field(default_factory=TTSSettings)
    hermes: HermesSettings = field(default_factory=HermesSettings)


def load_config(config_path: str | Path | None = None) -> AppConfig:
    """Load configuration from a YAML file or environment defaults."""
    if config_path is None:
        env_path = os.getenv("NEXO_CONFIG_PATH") or os.getenv("JARVIS_CONFIG_PATH")
        if env_path:
            config_path = Path(env_path)
        else:
            default_locations = [
                Path("config.yaml"),
                Path("config.example.yaml"),
                Path(__file__).parent.parent.parent / "config.example.yaml",
            ]
            for loc in default_locations:
                if loc.exists():
                    config_path = loc
                    break

    if config_path and Path(config_path).exists():
        with open(config_path, "r", encoding="utf-8") as f:
            raw: Dict[str, Any] = yaml.safe_load(f) or {}
            server_data = raw.get("server", {})
            stt_data = raw.get("stt", {})
            tts_data = raw.get("tts", {})
            hermes_data = raw.get("hermes", {})

            return AppConfig(
                server=ServerSettings(**server_data),
                stt=STTSettings(**stt_data),
                tts=TTSSettings(**tts_data),
                hermes=HermesSettings(**hermes_data),
            )

    return AppConfig()
