"""Source/resource regressions only; these do not execute Kotlin or Android."""
from pathlib import Path
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

ANDROID = Path(__file__).resolve().parents[2] / "android/app/src"
CONFIG = ANDROID / "main/java/id/my/yhdev/jarvis/config"


def test_bundled_endpoint_is_public_tls_websocket():
    resources = list((ANDROID / "main/res").glob("values*/strings.xml"))
    found = []
    for path in resources:
        for element in ET.parse(path).getroot().findall("string"):
            if element.get("name") == "default_server_url":
                url = urlsplit(element.text or "")
                assert url.scheme == "wss", path
                assert url.hostname == "jarvis.yhdev.my.id", path
                assert not url.username and not url.query and not url.fragment, path
                found.append(path)
    assert found


def test_fallback_does_not_assert_on_configuration_input():
    source = (CONFIG / "RemoteConfigManager.kt").read_text()
    fallback = source.split("private fun safeFallback()", 1)[1].split("private fun validated", 1)[0]
    assert "check(" not in fallback
    assert "ServerEndpointPolicy.fallback" in fallback
    assert "onUnavailable" in source
