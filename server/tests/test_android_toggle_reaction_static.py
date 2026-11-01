"""Source-only toggle reaction guards; no Android rendering or compilation."""
from pathlib import Path
import xml.etree.ElementTree as ET

MAIN = Path(__file__).resolve().parents[2] / 'android/app/src/main'


def _resolve_file(*candidates):
    for c in candidates:
        p = MAIN / c
        if p.exists():
            return p
    raise FileNotFoundError(f"None found from candidates: {candidates}")


def test_toggle_has_separate_caption_and_does_not_overwrite_reply():
    activity_file = _resolve_file(
        'java/id/my/yhdev/nexo/MainActivity.kt',
    )
    activity = activity_file.read_text()
    callback = activity.split('override fun onVoiceEnabledChanged', 1)[1].split('private fun renderStatus', 1)[0]
    assert 'visualizerView.setVoiceEnabled(' in callback
    assert 'R.string.voice_mood_on' in callback and 'R.string.voice_mood_off' in callback
    assert 'tvSubtitle.text =' not in callback
    root = ET.parse(MAIN / 'res/layout/activity_main.xml').getroot()
    a = '{http://schemas.android.com/apk/res/android}'
    caption = next(e for e in root.iter() if e.get(a + 'id') == '@+id/voiceMood')
    assert caption.get(a + 'layout_height') == 'wrap_content'
    assert caption.get(a + 'accessibilityLiveRegion') == 'polite'


def test_on_caption_expires_after_15_seconds_without_duplicate_callback_extension():
    activity_file = _resolve_file(
        'java/id/my/yhdev/nexo/MainActivity.kt',
    )
    activity = activity_file.read_text()
    callback = activity.split('override fun onVoiceEnabledChanged', 1)[1].split('private fun renderStatus', 1)[0]
    assert 'val firstSync = !voiceStateKnown' in callback
    assert 'if (firstSync || changed)' in callback
    assert 'voiceMoodDeadline = if (enabled) SystemClock.elapsedRealtime() + 15_000L else 0L' in callback
    assert 'renderVoiceMood()' in callback
    mood = activity.split('private fun renderVoiceMood()', 1)[1].split('private fun renderStatus', 1)[0]
    assert 'voiceMoodDeadline - SystemClock.elapsedRealtime()' in mood
    assert 'if (voiceEnabled && remaining <= 0L) View.GONE else View.VISIBLE' in mood
    assert 'uiHandler.removeCallbacks(hideVoiceMood)' in mood
    assert 'uiHandler.postDelayed(hideVoiceMood, remaining)' in mood
    resume = activity.split('override fun onResume()', 1)[1].split('private fun hideSystemUI', 1)[0]
    assert 'renderVoiceMood()' in resume
    assert 'override fun onStop()' in resume
    assert 'uiHandler.removeCallbacks(hideVoiceMood)' in resume
    destroy = activity.split('override fun onDestroy()', 1)[1]
    assert 'uiHandler.removeCallbacks(hideVoiceMood)' in destroy


def test_console_starts_hidden_and_visibility_only_follows_voice_enable():
    root = ET.parse(MAIN / 'res/layout/activity_main.xml').getroot()
    a = '{http://schemas.android.com/apk/res/android}'
    console = next(e for e in root.iter() if e.get(a + 'id') == '@+id/consoleCard')
    assert console.get(a + 'visibility') == 'gone'
    activity_file = _resolve_file(
        'java/id/my/yhdev/nexo/MainActivity.kt',
    )
    activity = activity_file.read_text()
    callback = activity.split('override fun onVoiceEnabledChanged', 1)[1].split('private fun renderStatus', 1)[0]
    assert 'consoleCard.visibility = if (enabled) View.VISIBLE else View.GONE' in callback
    assert 'lastAssistantReply =' not in callback
    transcript = activity.split('override fun onTranscriptionReceived', 1)[1].split('override fun onDestroy', 1)[0]
    assert 'consoleCard.visibility' not in transcript


def test_conversation_expression_lingers_then_yields_to_listening_error_or_off():
    view_file = _resolve_file(
        'java/id/my/yhdev/nexo/ui/VisualizerView.kt',
    )
    view = view_file.read_text()
    assert 'private var conversationExpression: MascotExpression? = null' in view
    assert 'val previousState = currentState' in view
    assert ('previousState == NexoState.SPEAKING' in view or 'previousState == JarvisState.SPEAKING' in view)
    assert 'conversationExpressionUntil = now + 6000L' in view
    assert (
        '(currentState == NexoState.IDLE || currentState == NexoState.LISTENING)' in view or
        '(currentState == JarvisState.IDLE || currentState == JarvisState.LISTENING)' in view
    )
    assert 'now < conversationExpressionUntil' in view
    assert (
        'NexoState.LISTENING -> MascotExpression.NETRAL' in view or
        'JarvisState.LISTENING -> MascotExpression.NETRAL' in view
    )
    assert (
        'state != NexoState.IDLE && state != NexoState.LISTENING' in view or
        'state != JarvisState.IDLE && state != JarvisState.LISTENING' in view
    )
    assert 'conversationExpression = null' in view
    assert (
        'if (state == NexoState.IDLE && previousState != NexoState.IDLE)' in view or
        'if (state == JarvisState.IDLE && previousState != JarvisState.IDLE)' in view
    )
    # Repainting cached status must not extend a response emotion forever.
    assert ('previousState != NexoState.IDLE' in view or 'previousState != JarvisState.IDLE' in view)


def test_paused_mascot_cries_and_optimistic_reaction_expires_without_audio_side_effects():
    view_file = _resolve_file(
        'java/id/my/yhdev/nexo/ui/VisualizerView.kt',
    )
    view = view_file.read_text()
    assert 'fun setVoiceEnabled(enabled: Boolean, animateReaction: Boolean)' in view
    assert '!voiceEnabled -> MascotExpression.MENANGIS' in view
    assert 'now < voiceReactionUntil -> MascotExpression.SANGAT_SENANG' in view
    assert '!enabled -> 0L' in view
    assert '!wasEnabled && animateReaction -> now + 2000L' in view
    assert 'else -> voiceReactionUntil' in view
    assert 'val wasEnabled = voiceEnabled' in view
    assert 'updateExpression(now)' in view
    assert 'if (!voiceEnabled || SystemClock.uptimeMillis() < voiceReactionUntil)' in view
    assert 'if (currentExpression == newExpr) return' in view
    assert 'startRecording(' not in view and 'startVoice(' not in view


def test_nexo_mascot_expressions_coverage():
    """Verify Nexo MascotExpression values cover visualizer requirements."""
    from nexo_server.mascot import MascotExpression
    expected = {
        "netral", "senang", "sangat_senang", "tertawa", "terkejut",
        "bingung", "sedih", "menangis", "marah", "takut", "malu",
        "cinta", "mengedip", "mengantuk", "jahil", "keren"
    }
    actual = {e.value for e in MascotExpression}
    assert expected.issubset(actual)
