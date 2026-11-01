"""Source/resource regressions only; these do not execute Kotlin or Android."""
from pathlib import Path
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

ANDROID = Path(__file__).resolve().parents[2] / "android/app/src"


def _get_config_dir():
    for candidate in [
        ANDROID / "main/java/id/my/yhdev/nexo/config",
    ]:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("Config directory not found")


CONFIG = _get_config_dir()


def test_bundled_endpoint_is_public_tls_websocket():
    resources = list((ANDROID / "main/res").glob("values*/strings.xml"))
    found = []
    for path in resources:
        for element in ET.parse(path).getroot().findall("string"):
            if element.get("name") == "default_server_url":
                url = urlsplit(element.text or "")
                assert url.scheme == "wss", path
                assert url.hostname in ("nexo.yhdev.my.id", "jarvis.yhdev.my.id"), path
                assert not url.username and not url.query and not url.fragment, path
                found.append(path)
    assert found


def test_bundled_app_name_matches_nexo():
    resources = list((ANDROID / "main/res").glob("values*/strings.xml"))
    found = []
    for path in resources:
        for element in ET.parse(path).getroot().findall("string"):
            if element.get("name") == "app_name":
                assert element.text in ("Nexo", "Jarvis"), path
                found.append(path)
    assert found


def test_fallback_does_not_assert_on_configuration_input():
    source = (CONFIG / "RemoteConfigManager.kt").read_text()
    fallback = source.split("private fun safeFallback()", 1)[1].split("private fun validated", 1)[0]
    assert "check(" not in fallback
    assert "ServerEndpointPolicy.fallback" in fallback
    assert "onUnavailable" in source


def test_nexo_endpoint_and_policy_constants():
    """Verify Nexo server endpoint path, policy, and protocol consistency."""
    from nexo_server.config import ServerSettings, HermesSettings
    from nexo_server.models.messages import ServerHello, NexoState, JarvisState

    cfg = ServerSettings()
    assert "/ws/appliance" in cfg.ws_path
    assert HermesSettings().session_name == "nexo"

    hello = ServerHello(session_id="nexo_test")
    assert hello.agent_name == "Nexo"
    assert hello.status == NexoState.IDLE
    assert JarvisState is NexoState
