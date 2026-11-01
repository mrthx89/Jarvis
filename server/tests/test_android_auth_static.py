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
    sheet = (ROOT / 'ui/CredentialSetupSheet.kt').read_text()
    assert 'CredentialSetupSheet(' in activity
    assert 'BottomSheetDialog(' in sheet
    assert 'FLAG_SECURE' in sheet
    assert 'setOnDismissListener' in sheet
    assert 'input.text?.clear()' in sheet
    assert 'setPositiveButton' in sheet
    assert 'Token perangkat' not in activity
    import xml.etree.ElementTree as ET
    layout = ROOT.parents[4] / 'res/layout/sheet_credential_setup.xml'
    elements = list(ET.parse(layout).getroot().iter())
    android = '{http://schemas.android.com/apk/res/android}'
    token = next(e for e in elements if e.get(android + 'id') == '@+id/setupToken')
    assert 'textPassword' in token.get(android + 'inputType')
    assert token.get(android + 'saveEnabled') == 'false'
    assert token.get(android + 'importantForAutofill') == 'noExcludeDescendants'
    assert token.get(android + 'maxLength') == '128'
    assert 'credentialStore.save(' in activity
    assert 'credentialStore.clear()' in activity
    assert 'credentialSheet?.dismiss()' in activity
    assert 'currentServerUrl == serverUrl' in activity
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
