"""Source-level safety guards only; not a substitute for Kotlin/device tests."""
from pathlib import Path
import xml.etree.ElementTree as ET

BASE_ANDROID = Path(__file__).resolve().parents[2] / 'android/app/src'


def _resolve_file(*candidates):
    for c in candidates:
        p = BASE_ANDROID / c
        if p.exists():
            return p
    raise FileNotFoundError(f"None found from candidates: {candidates}")


def test_keystore_encryption_and_origin_binding_are_wired():
    target = _resolve_file(
        'main/java/id/my/yhdev/nexo/security/DeviceCredentialStore.kt',
    )
    source = target.read_text()
    assert 'AndroidKeyStore' in source
    assert 'AES/GCM/NoPadding' in source
    assert 'updateAAD' in source
    assert 'origin(serverUrl)' in source
    assert 'savedOrigin != requestedOrigin' in source
    assert 'setRandomizedEncryptionRequired(true)' in source
    assert '.putString("token"' not in source
    assert 'Log.' not in source


def test_client_auth_denial_stops_retry_and_redirects_disabled():
    target = _resolve_file(
        'main/java/id/my/yhdev/nexo/network/NexoWebSocketClient.kt',
        'main/java/id/my/yhdev/nexo/network/NexoWebSocketClient.kt',
    )
    source = target.read_text()
    assert '.header("Authorization", "Bearer $bearerToken")' in source
    assert '.header("X-Device-ID", deviceId)' in source
    assert '.followRedirects(false)' in source
    assert '.followSslRedirects(false)' in source
    assert 'response?.code == 401 || response?.code == 403' in source
    assert 'shouldRun.set(false)' in source[source.index('private fun setupRequired'):]
    assert 'if (code == 1008)' in source
    assert 'Log.e(TAG, "WebSocket failure: ${t.message}")' not in source


def test_setup_dialog_and_service_reset_wiring():
    activity_file = _resolve_file(
        'main/java/id/my/yhdev/nexo/MainActivity.kt',
    )
    activity = activity_file.read_text()
    service_file = _resolve_file(
        'main/java/id/my/yhdev/nexo/service/NexoKioskService.kt',
        'main/java/id/my/yhdev/nexo/service/NexoKioskService.kt',
    )
    service = service_file.read_text()
    sheet_file = _resolve_file(
        'main/java/id/my/yhdev/nexo/ui/CredentialSetupSheet.kt',
    )
    sheet = sheet_file.read_text()

    assert 'setOnLongClickListener' in activity
    assert 'CredentialSetupSheet(' in activity
    assert 'BottomSheetDialog(' in sheet
    assert 'FLAG_SECURE' in sheet
    assert 'setOnDismissListener' in sheet
    assert 'input.text?.clear()' in sheet
    assert 'setPositiveButton' in sheet
    assert 'Token perangkat' not in activity

    layout = BASE_ANDROID / 'main/res/layout/sheet_credential_setup.xml'
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
    connection = service.split('private fun ensureConnection()', 1)[1].split('private var sheetOwner', 1)[0]
    assert 'val key = connectionKey ?: run { fail(' in connection
    assert 'credentialStore.read(key.first, key.second) ?: run { fail(' in connection
    assert connection.index('credentialStore.read(') < connection.index('NexoWebSocketClient(key.first, key.second, bearer, events)')
    refresh = service.split('fun refreshCredentials()', 1)[1].split('private fun ensureConnection()', 1)[0]
    assert 'closeRecordingSheet("credentials_changed")' in refresh
    assert 'pauseLocal("credentials_changed")' in refresh
    assert 'ensureConnection(' not in refresh
    assert 'startVoice(' not in refresh
    pause = service.split('private fun pauseLocal(', 1)[1].split('private fun fail(', 1)[0]
    assert 'persistOff()' in pause
    assert 'ownership.disconnected()' in pause
    assert 'webSocketClient?.disconnect(); webSocketClient = null' in pause


def test_network_junit_constructor_and_auth_cases_are_updated():
    tests = _resolve_file(
        'test/java/id/my/yhdev/nexo/network/NexoWebSocketClientTest.kt',
        'test/java/id/my/yhdev/nexo/network/NexoWebSocketClientTest.kt',
    )
    source = tests.read_text()
    assert ', "test", events)' not in source
    assert 'getHeader("Authorization")' in source
    assert 'getHeader("X-Device-ID")' in source
    assert 'for (status in listOf(401, 403))' in source
    assert 'webSocket.close(1008' in source


def test_nexo_security_and_auth_constants():
    """Verify Nexo auth and device credentials support Nexo security requirements."""
    from nexo_server.auth import DEVICE_ID, TOKEN, credential_origin
    assert DEVICE_ID.fullmatch("nexo_appliance_01")
    token = "x" * 43
    assert TOKEN.fullmatch(token)
    assert credential_origin("wss://nexo.yhdev.my.id/ws/appliance") == "wss://nexo.yhdev.my.id:443"
