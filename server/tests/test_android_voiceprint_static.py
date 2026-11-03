"""Static regression tests for Android Voiceprint Management and Biometric HUD UI."""
from pathlib import Path
import xml.etree.ElementTree as ET

MAIN = Path(__file__).resolve().parents[2] / 'android/app/src/main'
A = '{http://schemas.android.com/apk/res/android}'


def by_id(root, name):
    return next(e for e in root.iter() if e.get(A + 'id') == '@+id/' + name)


def test_voiceprint_hud_pill_exists_and_is_bounded():
    root = ET.parse(MAIN / 'res/layout/activity_main.xml').getroot()
    pill = by_id(root, 'pillVoiceprint')
    assert pill.get(A + 'layout_width') == 'wrap_content'
    assert pill.get('{http://schemas.android.com/apk/res-auto}layout_constrainedWidth') == 'true'
    status_text = by_id(root, 'tvVoiceprintStatus')
    assert status_text.get(A + 'textSize') == '12sp'
    assert status_text.get(A + 'textAllCaps') == 'false'
    assert by_id(root, 'ivVoiceprintLed') is not None


def test_voiceprint_sheet_layout_elements():
    root = ET.parse(MAIN / 'res/layout/sheet_voiceprint_management.xml').getroot()
    for name in ('cardVoiceprintStatus', 'ivSheetStatusLed', 'tvVoiceprintCount',
                 'tvVoiceprintDetails', 'layoutVoiceprintMeta', 'cardEnrollHint',
                 'btnVoiceprintUnenroll', 'btnVoiceprintClose'):
        elem = by_id(root, name)
        assert elem is not None, f"Missing element {name}"


def test_protocol_models_contain_voiceprint_dialog():
    models_file = MAIN / 'java/id/my/yhdev/nexo/network/ProtocolModels.kt'
    content = models_file.read_text()
    assert 'data class VoiceprintDialog' in content
    assert 'data class VoiceprintMetadata' in content
    assert 'data class VoiceprintQuery' in content
    assert 'data class VoiceprintUnenroll' in content


def test_main_activity_handles_voiceprint_dialog_and_sheet():
    activity_file = MAIN / 'java/id/my/yhdev/nexo/MainActivity.kt'
    content = activity_file.read_text()
    assert 'onVoiceprintDialogReceived' in content
    assert 'showVoiceprintManagement' in content
    assert 'VoiceprintManagementSheet' in content
    assert 'pillVoiceprint' in content
