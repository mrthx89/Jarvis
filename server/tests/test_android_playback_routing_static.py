"""Source guards, not Android execution or acoustic verification."""
from pathlib import Path

JAVA = Path(__file__).resolve().parents[2] / 'android/app/src/main/java/id/my/yhdev/nexo'


def test_pcm_backpressure_never_runs_on_ui_dispatch():
    s = (JAVA / 'service/NexoKioskService.kt').read_text()
    body = s.split('override fun onAudioChunkReceived(audioData: ByteArray)', 1)[1].split('override fun onVoiceprintDialogReceived', 1)[0]
    assert 'dispatch { this@NexoKioskService.onAudioChunkReceived' not in body
    assert 'dispatch { this@NexoKioskService.onTTSEnded' not in body
    assert 'dispatch { this@NexoKioskService.onTTSStarted' not in body
    assert 'token == generation' in body


def test_old_turn_drain_cannot_reopen_new_turn_microphone():
    s = (JAVA / 'service/NexoKioskService.kt').read_text()
    assert 'private val playbackTurn = AtomicInteger(0)' in s
    assert 'playbackTurn.incrementAndGet()' in s
    assert 'turn == playbackTurn.get()' in s


def test_active_earcons_stop_before_speech():
    e = (JAVA / 'audio/EarconManager.kt').read_text()
    assert 'fun stop()' in e
    assert 'soundPool?.stop(' in e
    s = (JAVA / 'service/NexoKioskService.kt').read_text()
    tts = s.rsplit('override fun onTTSStarted() {', 1)[1].split('override fun onTTSEnded()', 1)[0]
    assert 'earconManager.stop()' in tts
