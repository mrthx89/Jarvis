"""Source-level layout regressions; these do not render Android or compile Kotlin."""
from pathlib import Path
import xml.etree.ElementTree as ET

MAIN = Path(__file__).resolve().parents[2] / 'android/app/src/main'
A = '{http://schemas.android.com/apk/res/android}'


def by_id(root, name):
    return next(e for e in root.iter() if e.get(A + 'id') == '@+id/' + name)


def test_sheet_actions_are_full_width_multiline_not_weighted_columns():
    root = ET.parse(MAIN / 'res/layout/sheet_credential_setup.xml').getroot()
    for name in ('setupCopyId', 'setupHelp', 'setupSave', 'setupCancel', 'setupReset'):
        button = by_id(root, name)
        assert button.get(A + 'layout_width') == 'match_parent', name
        assert button.get(A + 'singleLine') == 'false', name
        assert button.get(A + 'layout_height') == 'wrap_content', name
        assert button.get(A + 'layout_weight') is None, name
    assert root.get(A + 'layout_height') == 'match_parent'


def test_token_label_and_helper_wrap_outside_the_single_line_input():
    root = ET.parse(MAIN / 'res/layout/sheet_credential_setup.xml').getroot()
    label = by_id(root, 'setupTokenLabel')
    assert label.get(A + 'labelFor') == '@id/setupToken'
    assert label.get(A + 'layout_height') == 'wrap_content'
    helper = by_id(root, 'setupTokenHelper')
    assert helper.get(A + 'layout_height') == 'wrap_content'
    assert helper.get(A + 'maxLines') is None
    field = by_id(root, 'setupTokenLayout')
    assert field.get(A + 'hint') is None
    assert field.get('{http://schemas.android.com/apk/res-auto}hintEnabled') == 'false'
    sheet = (MAIN / 'java/id/my/yhdev/jarvis/ui/CredentialSetupSheet.kt').read_text()
    assert 'ViewGroup.LayoutParams.MATCH_PARENT' in sheet
    assert 'WindowInsetsCompat.Type.displayCutout()' in sheet
    assert 'SOFT_INPUT_ADJUST_RESIZE' in sheet
    assert 'bars.left' in sheet and 'bars.right' in sheet


def test_sheet_does_not_use_error_max_lines_api_absent_in_material_1_11():
    sheet = (MAIN / 'java/id/my/yhdev/jarvis/ui/CredentialSetupSheet.kt').read_text()
    assert '.errorMaxLines' not in sheet
    assert 'setErrorMaxLines(' not in sheet


def test_copy_action_uses_last_final_assistant_reply_not_status_or_user_text():
    root = ET.parse(MAIN / 'res/layout/activity_main.xml').getroot()
    button = by_id(root, 'copyReply')
    assert button.get(A + 'enabled') == 'false'
    assert button.get(A + 'layout_width') == 'match_parent'
    activity = (MAIN / 'java/id/my/yhdev/jarvis/MainActivity.kt').read_text()
    assert 'if (role == "assistant" && isFinal)' in activity
    assert 'lastAssistantReply = text' in activity
    assert 'ClipData.newPlainText(getString(R.string.reply_clip_label), lastAssistantReply)' in activity
    assert 'copyReply.isEnabled = text.isNotBlank()' in activity
    assert 'tvSubtitle.text.toString()' not in activity


def test_status_notch_is_content_sized_bounded_and_voice_button_is_explicit():
    root = ET.parse(MAIN / 'res/layout/activity_main.xml').getroot()
    pill = by_id(root, 'pillStatus')
    assert pill.get(A + 'layout_width') == 'wrap_content'
    assert pill.get('{http://schemas.android.com/apk/res-auto}layout_constrainedWidth') == 'true'
    status = by_id(root, 'tvStatus')
    assert status.get(A + 'textAllCaps') == 'false'
    assert status.get(A + 'textSize') == '12sp'
    toggle = by_id(root, 'toggleVoice')
    assert toggle.get(A + 'enabled') == 'false'
    assert toggle.get(A + 'minHeight') == '48dp'
    activity = (MAIN / 'java/id/my/yhdev/jarvis/MainActivity.kt').read_text()
    assert 'service.stopVoice()' in activity and 'service.startVoice()' in activity
    assert 'override fun onVoiceEnabledChanged(enabled: Boolean)' in activity
    assert 'TransitionManager.beginDelayedTransition' in activity
    assert 'tvStatus.contentDescription = message' in activity


def test_enabled_only_callback_renders_cached_status_without_callback_order_dependency():
    activity = (MAIN / 'java/id/my/yhdev/jarvis/MainActivity.kt').read_text()
    state_callback = activity.split('override fun onStateChanged(', 1)[1].split('override fun onVoiceEnabledChanged', 1)[0]
    enabled_callback = activity.split('override fun onVoiceEnabledChanged', 1)[1].split('override fun onTelemetryReceived', 1)[0]
    assert 'lastState = state' in state_callback
    assert 'renderStatus()' in state_callback
    assert 'renderStatus()' in enabled_callback
    assert 'private fun renderStatus()' in activity
    assert 'state == JarvisState.ERROR -> R.string.voice_error' in activity
