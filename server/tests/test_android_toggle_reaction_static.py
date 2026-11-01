"""Source-only toggle reaction guards; no Android rendering or compilation."""
from pathlib import Path
import xml.etree.ElementTree as ET

MAIN = Path(__file__).resolve().parents[2] / 'android/app/src/main'
UI = MAIN / 'java/id/my/yhdev/jarvis/ui'


def test_toggle_has_separate_caption_and_does_not_overwrite_reply():
    activity = (MAIN / 'java/id/my/yhdev/jarvis/MainActivity.kt').read_text()
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
    activity = (MAIN / 'java/id/my/yhdev/jarvis/MainActivity.kt').read_text()
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
    activity = (MAIN / 'java/id/my/yhdev/jarvis/MainActivity.kt').read_text()
    callback = activity.split('override fun onVoiceEnabledChanged', 1)[1].split('private fun renderStatus', 1)[0]
    assert 'consoleCard.visibility = if (enabled) View.VISIBLE else View.GONE' in callback
    assert 'lastAssistantReply =' not in callback
    transcript = activity.split('override fun onTranscriptionReceived', 1)[1].split('override fun onDestroy', 1)[0]
    assert 'consoleCard.visibility' not in transcript


def test_conversation_expression_lingers_then_yields_to_listening_error_or_off():
    view = (UI / 'VisualizerView.kt').read_text()
    assert 'private var conversationExpression: MascotExpression? = null' in view
    assert 'val previousState = currentState' in view
    assert 'previousState == JarvisState.SPEAKING' in view
    assert 'conversationExpressionUntil = now + 6000L' in view
    assert '(currentState == JarvisState.IDLE || currentState == JarvisState.LISTENING)' in view
    assert 'now < conversationExpressionUntil' in view
    assert 'JarvisState.LISTENING -> MascotExpression.NETRAL' in view
    assert 'state != JarvisState.IDLE && state != JarvisState.LISTENING' in view
    assert 'conversationExpression = null' in view
    assert 'if (state == JarvisState.IDLE && previousState != JarvisState.IDLE)' in view
    # Repainting cached status must not extend a response emotion forever.
    assert 'previousState != JarvisState.IDLE' in view


def test_paused_mascot_cries_and_optimistic_reaction_expires_without_audio_side_effects():
    view = (UI / 'VisualizerView.kt').read_text()
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
