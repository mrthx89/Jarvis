"""Source-level layout regressions; these do not render Android or compile Kotlin."""
from pathlib import Path
import xml.etree.ElementTree as ET

MAIN = Path(__file__).resolve().parents[2] / 'android/app/src/main'
A = '{http://schemas.android.com/apk/res/android}'


def _resolve_file(*candidates):
    for c in candidates:
        p = MAIN / c
        if p.exists():
            return p
    raise FileNotFoundError(f"None found from candidates: {candidates}")


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
    sheet_file = _resolve_file(
        'java/id/my/yhdev/nexo/ui/CredentialSetupSheet.kt',
    )
    sheet = sheet_file.read_text()
    assert 'ViewGroup.LayoutParams.MATCH_PARENT' in sheet
    assert 'WindowInsetsCompat.Type.displayCutout()' in sheet
    assert 'SOFT_INPUT_ADJUST_RESIZE' in sheet
    assert 'bars.left' in sheet and 'bars.right' in sheet


def test_sheet_does_not_use_error_max_lines_api_absent_in_material_1_11():
    sheet_file = _resolve_file(
        'java/id/my/yhdev/nexo/ui/CredentialSetupSheet.kt',
    )
    sheet = sheet_file.read_text()
    assert '.errorMaxLines' not in sheet
    assert 'setErrorMaxLines(' not in sheet


def test_copy_action_uses_last_final_assistant_reply_not_status_or_user_text():
    root = ET.parse(MAIN / 'res/layout/activity_main.xml').getroot()
    button = by_id(root, 'copyReply')
    assert button.get(A + 'enabled') == 'false'
    assert button.tag == 'androidx.appcompat.widget.AppCompatImageButton'
    assert button.get(A + 'layout_width') == '48dp'
    assert button.get(A + 'layout_height') == '48dp'
    assert button.get(A + 'src') in ('@drawable/ic_clipboard', '@drawable/ic_clipboard_24')
    assert button.get(A + 'contentDescription') == '@string/copy_reply'
    assert button.get(A + 'text') is None
    header = by_id(root, 'consoleHeader')
    assert header.get(A + 'orientation') == 'horizontal'
    assert [e.get(A + 'id') for e in header] == ['@+id/tvConsoleHeader', '@+id/copyReply']
    assert by_id(root, 'tvConsoleHeader').get(A + 'layout_weight') == '1'
    assert (MAIN / 'res/drawable/ic_clipboard.xml').exists()
    activity_file = _resolve_file(
        'java/id/my/yhdev/nexo/MainActivity.kt',
    )
    activity = activity_file.read_text()
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
    activity_file = _resolve_file(
        'java/id/my/yhdev/nexo/MainActivity.kt',
    )
    activity = activity_file.read_text()
    assert 'service.stopVoice()' in activity and 'service.startVoice()' in activity
    assert 'override fun onVoiceEnabledChanged(enabled: Boolean)' in activity
    assert 'TransitionManager.beginDelayedTransition' in activity
    assert 'tvStatus.contentDescription = message' in activity


def test_enabled_only_callback_renders_cached_status_without_callback_order_dependency():
    activity_file = _resolve_file(
        'java/id/my/yhdev/nexo/MainActivity.kt',
    )
    activity = activity_file.read_text()
    state_callback = activity.split('override fun onStateChanged(', 1)[1].split('override fun onVoiceEnabledChanged', 1)[0]
    enabled_callback = activity.split('override fun onVoiceEnabledChanged', 1)[1].split('override fun onTelemetryReceived', 1)[0]
    assert 'lastState = state' in state_callback
    assert 'renderStatus()' in state_callback
    assert 'renderStatus()' in enabled_callback
    assert 'private fun renderStatus()' in activity
    assert (
        'state == NexoState.ERROR -> R.string.voice_error' in activity or
        'state == NexoState.ERROR -> R.string.voice_error' in activity
    )


def test_nexo_state_mapping_static():
    """Verify Nexo states exist and map cleanly for mobile layout rendering."""
    from nexo_server.models.messages import NexoState
    assert all(hasattr(NexoState, s) for s in ("IDLE", "LISTENING", "THINKING", "SPEAKING", "ERROR", "DISCONNECTED"))


def test_sheet_app_version_and_support_button():
    """Verify bottom sheet includes version info and Buy me a coffee support button linking to yapp.ink/yhdev."""
    root = ET.parse(MAIN / 'res/layout/sheet_credential_setup.xml').getroot()
    support_btn = by_id(root, 'setupSupportButton')
    assert support_btn is not None
    assert support_btn.get(A + 'layout_width') == 'match_parent'
    assert support_btn.get(A + 'minHeight') == '48dp'

    version_view = by_id(root, 'setupAppVersion')
    assert version_view is not None

    sheet_code = (MAIN / 'java/id/my/yhdev/nexo/ui/CredentialSetupSheet.kt').read_text()
    assert 'https://yapp.ink/yhdev' in sheet_code
    assert 'setupAppVersion' in sheet_code
    assert 'setupSupportButton' in sheet_code
    assert 'isTablet' in sheet_code or 'maxSheetWidth' in sheet_code


def test_three_zone_simple_mobile_layout_and_contextual_controls():
    """Verify top status bar, central mascot focal point, bottom thumb-zone dock, and hidden inactive manual controls."""
    APP = '{http://schemas.android.com/apk/res-auto}'
    root = ET.parse(MAIN / 'res/layout/activity_main.xml').getroot()
    pill_status = by_id(root, 'pillStatus')
    pill_voiceprint = by_id(root, 'pillVoiceprint')
    visualizer = by_id(root, 'visualizerView')
    capture_controls = by_id(root, 'captureControls')
    start_utterance = by_id(root, 'startUtterance')
    finish_utterance = by_id(root, 'finishUtterance')
    toggle_voice = by_id(root, 'toggleVoice')

    assert pill_status.get(APP + 'layout_constraintTop_toTopOf') == 'parent'
    assert pill_voiceprint.get(APP + 'layout_constraintTop_toTopOf') == 'parent'
    assert visualizer.get(APP + 'layout_constraintTop_toBottomOf') == '@id/pillStatus'
    assert visualizer.get(APP + 'layout_constraintBottom_toTopOf') == '@id/voiceMood'
    assert capture_controls.get(A + 'visibility') == 'gone'
    assert start_utterance.get(A + 'visibility') == 'gone'
    assert finish_utterance.get(A + 'visibility') == 'gone'
    assert toggle_voice.get(APP + 'layout_constraintBottom_toBottomOf') == 'parent'

    activity = (MAIN / 'java/id/my/yhdev/nexo/MainActivity.kt').read_text()
    assert 'captureMode.visibility = View.GONE' in activity
    assert 'captureControls.visibility = if (voiceEnabled && !isSleeping && (showManualStart || canFinish)) View.VISIBLE else View.GONE' in activity
    assert 'startUtterance.visibility = if (showManualStart) View.VISIBLE else View.GONE' in activity
    assert 'finishUtterance.visibility = if (canFinish) View.VISIBLE else View.GONE' in activity

    service = (MAIN / 'java/id/my/yhdev/nexo/service/NexoKioskService.kt').read_text()
    assert 'private var automatic = true' in service
    assert 'if (!modeConfiguredByUser) automatic = vadAvailable' in service
