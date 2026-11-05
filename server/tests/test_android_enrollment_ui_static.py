"""Source contracts only: no Kotlin compilation or Android rendering."""
from pathlib import Path
import re
import xml.etree.ElementTree as ET

MAIN = Path(__file__).resolve().parents[2] / 'android/app/src/main'
JAVA = MAIN / 'java/id/my/yhdev/nexo'


def test_enroll_controls_are_lease_bound_v2_not_legacy_stubs():
    source = (JAVA / 'network/NexoWebSocketClient.kt').read_text()
    for action in ('startVoiceprintEnrollment', 'finishVoiceprintEnrollment', 'cancelVoiceprintEnrollment'):
        assert re.search(r'fun ' + action + r'\(requestId: String\): Boolean = false', source)
    service = (JAVA / 'service/NexoKioskService.kt').read_text()
    assert 'sendEnrollment("enrollment_begin", enrollmentRequest!!)' in service
    assert 'sendEnrollment("enrollment_save", enrollmentRequest!!,' in service
    assert 'sendOutdoorControl("enrollment_cancel", uuid(),' in service
    sender = service.split('private fun sendEnrollment(', 1)[1].split('fun saveVoiceprintEnrollment()', 1)[0]
    assert '"owner_id" to (sheetOwner ?: return false)' in sender
    assert '"lease_id" to (ownership.lease() ?: return false)' in sender
    assert '"enrollment_id" to (enrollmentId ?: return false)' in sender
    assert 'sendOutdoorControl(type, request, fields)' in sender
    for action in ('startVoiceprintEnrollment', 'finishVoiceprintEnrollment', 'cancelVoiceprintEnrollment'):
        assert not re.search(r'\b(?:client|webSocketClient)\??\.' + action + r'\(', service)
    models = (JAVA / 'network/ProtocolModels.kt').read_text()
    for field in ('request_id', 'success', 'available'):
        assert '@SerializedName("' + field + '")' in models


def test_sheet_waits_for_server_before_recording_and_before_success():
    source = (JAVA / 'ui/VoiceprintManagementSheet.kt').read_text()
    assert 'onEnroll: () -> Boolean' in source
    assert 'onFinish: () -> Boolean' in source
    assert 'onCancel: () -> Unit' in source
    assert 'RecordingSession("ACQUIRING", "Menjeda percakapan…", false' in source
    assert 'session = value' in source
    assert 'feedback.text = value.message' in source
    assert 'val ready = session.ready' in source
    assert 'enroll.isEnabled = ready &&' in source
    assert 'finish.isEnabled = state == "RECORDING"' in source
    assert 'save.isEnabled = ready && state == "SAMPLES_READY"' in source
    update = source.split('fun updateDialog(', 1)[1].split('fun updateInputLevel(', 1)[0]
    assert 'session =' not in update
    assert 'success == true' not in source
    assert 'Toast.makeText' not in source
    assert 'onCancel()' in source
    assert 'onFinish()' in source
    assert 'onSave()' in source
    service = (JAVA / 'service/NexoKioskService.kt').read_text()
    capture = service.split('private fun startCapture(', 1)[1].split('private var activeCaptureGeneration', 1)[0]
    assert 'if (purpose != "conversation" && !ownership.dedicatedAllowed(now())) return false' in capture
    assert capture.index('val started = recorder.startRecording()') < capture.index('!started || !recorder.isRecording()') < capture.index('else sheet("RECORDING"')
    ack = service.split('"enrollment_ack" ->', 1)[1].split('"utterance_ack" ->', 1)[0]
    for fence in ('!ownership.dedicatedAllowed(now())', 'request != enrollmentRequest',
                  '!= sheetOwner', '!= ownership.lease()', '!= enrollmentId'):
        assert fence in ack
    assert '"saved" -> if (enrollmentOperation == "save")' in ack
    assert 'sheet("SAVED",' in ack
    save = service.split('fun saveVoiceprintEnrollment()', 1)[1].split('fun startDiagnosticCapture()', 1)[0]
    assert 'sheet("SAVING",' in save
    assert 'sheet("SAVED",' not in save
    assert '"sample_utterance_ids" to samples.take(sampleCount)' in save
    assert '"holdout_utterance_id" to samples.last()' in save


def test_recording_ui_resources_and_service_callbacks_are_wired():
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


def test_hud_actions_have_48dp_targets_and_explicit_native_focus():
    ns = '{http://schemas.android.com/apk/res/android}'
    root = ET.parse(MAIN / 'res/layout/activity_main.xml').getroot()
    nodes = {el.get(ns + 'id', '').split('/')[-1]: el for el in root.iter()}
    for name in ('pillStatus', 'pillVoiceprint', 'toggleVoice', 'captureMode',
                 'startUtterance', 'finishUtterance', 'copyReply'):
        node = nodes[name]
        assert node.get(ns + 'minHeight', node.get(ns + 'layout_height')) in ('32dp', '48dp'), name
        assert node.get(ns + 'minWidth', node.get(ns + 'layout_width')) == '48dp', name
        assert node.get(ns + 'focusable') == 'true', name
        assert node.get(ns + 'background') in ('@drawable/bg_hud_control', '@drawable/bg_status_pill'), name
    activity = (JAVA / 'MainActivity.kt').read_text()
    assert 'pillStatus.setOnClickListener' in activity
    assert 'pillStatus.setOnLongClickListener' in activity
    assert 'R.string.status_setup_action' in activity
    assert 'AccessibilityNodeInfoCompat.AccessibilityActionCompat.ACTION_LONG_CLICK' in activity
    assert 'pillStatus.contentDescription = message ?: status' in activity
    assert 'val targetAlpha = 1.0f' in activity


def test_hud_control_edges_meet_nontext_contrast_in_all_enabled_states():
    ns = '{http://schemas.android.com/apk/res/android}'
    def luminance(color):
        rgb = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
        return sum(c * w for c, w in zip(linear, (0.2126, 0.7152, 0.0722)))
    def contrast(a, b):
        x, y = sorted((luminance(a), luminance(b)))
        return (y + 0.05) / (x + 0.05)
    root = ET.parse(MAIN / 'res/drawable/bg_hud_control.xml').getroot()
    assert root.tag == 'selector'
    assert any(item.get(ns + 'state_focused') == 'true' for item in root)
    assert any(item.get(ns + 'state_pressed') == 'true' for item in root)
    for item in root:
        shape = item.find('shape')
        assert shape is not None
        stroke = shape.find('stroke')
        solid = shape.find('solid')
        assert stroke is not None and solid is not None
        assert int(stroke.attrib[ns + 'width'].removesuffix('dp')) >= 2
        fills = [solid.attrib[ns + 'color']]
        for background in (*fills, '#000000', '#1E2838'):
            assert contrast(stroke.attrib[ns + 'color'], background) >= 3.0
    assert contrast('#E0E6ED', '#2A394E') >= 4.5
    assert contrast('#00E5FF', '#2A394E') >= 4.5


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
