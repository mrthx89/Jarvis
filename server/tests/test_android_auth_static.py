"""Source-level safety guards only; not a substitute for Kotlin/device tests."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / 'android/app/src/main/java/id/my/yhdev/jarvis'


def test_keystore_encryption_and_origin_binding_are_wired():
    source = (ROOT / 'security/DeviceCredentialStore.kt').read_text()
    assert 'AndroidKeyStore' in source
    assert 'AES/GCM/NoPadding' in source
    assert 'updateAAD' in source
    assert 'origin(serverUrl)' in source
    assert 'savedOrigin != requestedOrigin' in source
    assert 'setRandomizedEncryptionRequired(true)' in source
    assert '.putString("token"' not in source
    assert 'Log.' not in source


def test_client_auth_denial_stops_retry_and_redirects_disabled():
    source = (ROOT / 'network/JarvisWebSocketClient.kt').read_text()
    assert '.header("Authorization", "Bearer $bearerToken")' in source
    assert '.header("X-Device-ID", deviceId)' in source
    assert '.followRedirects(false)' in source
    assert '.followSslRedirects(false)' in source
    assert 'response?.code == 401 || response?.code == 403' in source
    assert 'shouldRun.set(false)' in source[source.index('private fun setupRequired'):]
    assert 'if (code == 1008)' in source
    assert 'Log.e(TAG, "WebSocket failure: ${t.message}")' not in source


def test_setup_dialog_and_service_reset_wiring():
    activity = (ROOT / 'MainActivity.kt').read_text()
    service = (ROOT / 'service/JarvisKioskService.kt').read_text()
    assert 'setOnLongClickListener' in activity
    assert 'TYPE_TEXT_VARIATION_PASSWORD' in activity
    assert 'FLAG_SECURE' in activity
    assert 'credentialStore.save(' in activity
    assert 'credentialStore.clear()' in activity
    assert 'refreshCredentials()' in activity
    assert 'credentialStore.read(serverUrl, deviceId)' in service
    assert 'forceReconnect = true' in service
    assert 'if (bearerToken == null)' in service

def test_network_junit_constructor_and_auth_cases_are_updated():
    tests = ROOT.parents[5] / 'test/java/id/my/yhdev/jarvis/network/JarvisWebSocketClientTest.kt'
    source = tests.read_text()
    assert ', "test", events)' not in source
    assert 'getHeader("Authorization")' in source
    assert 'getHeader("X-Device-ID")' in source
    assert 'for (status in listOf(401, 403))' in source
    assert 'webSocket.close(1008' in source
