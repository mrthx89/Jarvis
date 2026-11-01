"""Verification of Nexo Server modular structure, imports, mascot, and auth fallback."""

from pathlib import Path
import pytest


def test_nexo_server_imports():
    """All nexo_server modules must import cleanly without error."""
    import nexo_server
    import nexo_server.config
    import nexo_server.auth
    import nexo_server.mascot
    import nexo_server.models.messages
    import nexo_server.pipeline.vad
    import nexo_server.pipeline.stt
    import nexo_server.pipeline.tts
    import nexo_server.hermes.agent_bridge
    import nexo_server.gateway.websocket_handler
    import nexo_server.telegram_command
    import nexo_server.telegram_provision
    import nexo_server.main

    assert nexo_server.__version__ == "0.1.0"


def test_nexo_and_jarvis_mascot_commands():
    """Mascot parser must recognize 'nexo' wake-word while maintaining 'jarvis' fallback."""
    from nexo_server.mascot import MascotExpression, parse_expression_command

    # Nexo commands
    assert parse_expression_command("nexo, tersenyum") == MascotExpression.SENANG
    assert parse_expression_command("nexo, tertawa") == MascotExpression.TERTAWA
    assert parse_expression_command("Nexo tersenyum") == MascotExpression.SENANG
    assert parse_expression_command("tolong nexo, tersenyum dong") == MascotExpression.SENANG
    assert parse_expression_command("nexo tolong tampilkan ekspresi sangat senang ya") == MascotExpression.SANGAT_SENANG

    # Jarvis fallback commands
    assert parse_expression_command("jarvis, tersenyum") == MascotExpression.SENANG
    assert parse_expression_command("jarvis, tertawa") == MascotExpression.TERTAWA
    assert parse_expression_command("Jarvis tersenyum") == MascotExpression.SENANG

    # Generic commands
    assert parse_expression_command("tersenyum") == MascotExpression.SENANG
    assert parse_expression_command("ubah ekspresi ke mengantuk") == MascotExpression.MENGANTUK

    # Non-commands fall through
    assert parse_expression_command("nexo") is None
    assert parse_expression_command("jarvis") is None
    assert parse_expression_command("nexo nexo tersenyum") is None


def test_nexo_auth_fallback_to_jarvis_auth():
    """Registry path resolver must support /root/.config/nexo-auth/ with fallback to jarvis-auth."""
    from nexo_server.auth import resolve_registry_path, JARVIS_DEFAULT_REGISTRY, NEXO_DEFAULT_REGISTRY

    if NEXO_DEFAULT_REGISTRY.exists():
        assert resolve_registry_path("/root/.config/nexo-auth/") == NEXO_DEFAULT_REGISTRY
        assert resolve_registry_path("/root/.config/nexo-auth/devices.json") == NEXO_DEFAULT_REGISTRY
    elif JARVIS_DEFAULT_REGISTRY.exists():
        resolved_dir = resolve_registry_path("/root/.config/nexo-auth/")
        assert resolved_dir == JARVIS_DEFAULT_REGISTRY

        resolved_file = resolve_registry_path("/root/.config/nexo-auth/devices.json")
        assert resolved_file == JARVIS_DEFAULT_REGISTRY


def test_nexo_auth_fallback_custom_dirs(tmp_path):
    """Registry path resolver must fallback from nexo-auth to peer jarvis-auth."""
    from nexo_server.auth import resolve_registry_path

    base = tmp_path / "auth_test"
    nexo_dir = base / "nexo-auth"
    jarvis_dir = base / "jarvis-auth"
    jarvis_dir.mkdir(parents=True, mode=0o700)
    jarvis_file = jarvis_dir / "devices.json"
    jarvis_file.write_text('{"version":1,"devices":{}}')

    # Target under nexo-auth should resolve to peer jarvis-auth when nexo devices.json doesn't exist
    target = nexo_dir / "devices.json"
    resolved = resolve_registry_path(target)
    assert resolved == jarvis_file

    # Once nexo devices.json exists, it should resolve to nexo
    nexo_dir.mkdir(parents=True, mode=0o700)
    target.write_text('{"version":1,"devices":{}}')
    resolved2 = resolve_registry_path(target)
    assert resolved2 == target


def test_systemd_nexo_service_unit():
    """nexo-appliance.service must be prepared in server/deploy/."""
    service_path = Path(__file__).resolve().parents[1] / "deploy/nexo-appliance.service"
    assert service_path.exists(), "nexo-appliance.service not found"
    content = service_path.read_text()
    assert "ExecStart=/data/home/Jarvis/server/.venv/bin/python -m nexo_server.main" in content
    assert "Environment=PYTHONPATH=/data/home/Jarvis/server/src" in content
    assert "WorkingDirectory=/data/home/Jarvis/server" in content


def test_nexo_server_config_and_constants():
    """Verify Nexo server configuration defaults and protocol constants."""
    from nexo_server.config import ServerSettings, HermesSettings, STTSettings, TTSSettings
    from nexo_server.models.messages import ServerHello, NexoState, JarvisState

    srv = ServerSettings()
    assert srv.ws_path == "/ws/appliance"
    assert srv.port == 8765

    hermes = HermesSettings()
    assert hermes.session_name == "nexo"

    stt = STTSettings()
    assert stt.language == "id"

    tts = TTSSettings()
    assert "id-ID" in tts.voice

    hello = ServerHello(session_id="nexo_test_01")
    assert hello.agent_name == "Nexo"
    assert hello.status == NexoState.IDLE
    assert JarvisState is NexoState


def test_nexo_pyproject_toml():
    """Verify pyproject.toml defines nexo-server package."""
    import tomllib
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    assert pyproject_path.exists()
    data = tomllib.loads(pyproject_path.read_text())
    assert data["project"]["name"] == "nexo-server"
    assert "Nexo" in data["project"]["description"]


def test_nexo_dual_compatibility_all_modules():
    """Verify all legacy jarvis_server modules and nexo_server modules can be imported and are functional."""
    import jarvis_server
    import jarvis_server.config
    import jarvis_server.auth
    import jarvis_server.mascot
    import jarvis_server.models.messages
    import jarvis_server.pipeline.vad
    import jarvis_server.pipeline.stt
    import jarvis_server.pipeline.tts
    import jarvis_server.hermes.agent_bridge
    import jarvis_server.gateway.websocket_handler
    import jarvis_server.telegram_command
    import jarvis_server.telegram_provision
    import jarvis_server.main

    import nexo_server
    import nexo_server.config
    import nexo_server.auth
    import nexo_server.mascot
    import nexo_server.models.messages
    import nexo_server.pipeline.vad
    import nexo_server.pipeline.stt
    import nexo_server.pipeline.tts
    import nexo_server.hermes.agent_bridge
    import nexo_server.gateway.websocket_handler
    import nexo_server.telegram_command
    import nexo_server.telegram_provision
    import nexo_server.main

    assert jarvis_server.__version__ == nexo_server.__version__
    assert hasattr(jarvis_server.config, "AppConfig")
    assert hasattr(jarvis_server.auth, "DeviceRegistry")
    assert hasattr(jarvis_server.mascot, "MascotExpression")
    assert hasattr(jarvis_server.models.messages, "ClientHello")
    assert hasattr(jarvis_server.pipeline.vad, "EnergyVAD")
    assert hasattr(jarvis_server.pipeline.stt, "FasterWhisperSTT")
    assert hasattr(jarvis_server.pipeline.tts, "EdgeTTS")
    assert hasattr(jarvis_server.hermes.agent_bridge, "HermesBridge")
    assert hasattr(jarvis_server.gateway.websocket_handler, "ApplianceSession")
    assert hasattr(jarvis_server.telegram_command, "read_settings")
    assert hasattr(jarvis_server.telegram_provision, "handle_command")
    assert hasattr(jarvis_server.main, "app")
