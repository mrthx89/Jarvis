"""Source contracts only: no Kotlin compilation or Android rendering."""
from pathlib import Path
import re
import xml.etree.ElementTree as ET

MAIN = Path(__file__).resolve().parents[2] / 'android/app/src/main'
JAVA = MAIN / 'java/id/my/yhdev/nexo'


def test_enroll_controls_send_explicit_protocol_not_just_toast():
    source = (JAVA / 'network/NexoWebSocketClient.kt').read_text()
    for action in ('startVoiceprintEnrollment', 'finishVoiceprintEnrollment', 'cancelVoiceprintEnrollment'):
        assert re.search(r'fun ' + action + r'\(requestId: String\): Boolean', source)
    models = (JAVA / 'network/ProtocolModels.kt').read_text()
    for field in ('request_id', 'success', 'available'):
        assert '@SerializedName("' + field + '")' in models


def test_sheet_waits_for_server_before_recording_and_before_success():
    source = (JAVA / 'ui/VoiceprintManagementSheet.kt').read_text()
    assert 'onEnroll: () -> Boolean' in source
    assert 'onFinish: () -> Boolean' in source
    assert 'onCancel: () -> Unit' in source
    assert '"recording" ->' in source
    assert 'voiceDialog.success == true' in source
    assert 'Toast.makeText' not in source
    assert 'onCancel()' in source
    assert 'onFinish()' in source


def test_recording_ui_has_real_progress_level_cancel_and_status():
    root = ET.parse(MAIN / 'res/layout/sheet_voiceprint_management.xml').getroot()
    ns = '{http://schemas.android.com/apk/res/android}'
    ids = {el.get(ns + 'id') for el in root.iter()}
    for name in ('voiceprintFeedback', 'voiceprintProgress', 'voiceprintInputLevel', 'btnVoiceprintCancel'):
        assert '@+id/' + name in ids
    activity = (JAVA / 'MainActivity.kt').read_text()
    for call in ('startVoiceprintEnrollment()', 'finishVoiceprintEnrollment()', 'cancelVoiceprintEnrollment()'):
        assert call in activity


def test_backgrounding_dismisses_recording_sheet():
    activity = (JAVA / 'MainActivity.kt').read_text()
    on_stop = activity.split('override fun onStop()', 1)[1].split('private fun hideSystemUI', 1)[0]
    assert 'voiceprintSheet?.dismiss()' in on_stop


def test_android_resource_ids_exist_across_layouts():
    ns = '{http://schemas.android.com/apk/res/android}'
    ids = set()
    for file in (MAIN / 'res').rglob('*.xml'):
        for node in ET.parse(file).getroot().iter():
            value = node.get(ns + 'id', '')
            if value.startswith('@+id/'):
                ids.add(value.split('/')[-1])
    missing = []
    for source in JAVA.rglob('*.kt'):
        for match in re.finditer(r'(?<![\w.])R\.id\.(\w+)', source.read_text()):
            if match.group(1) not in ids:
                missing.append((source.name, match.group(1)))
    assert missing == []
