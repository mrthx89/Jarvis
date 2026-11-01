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
