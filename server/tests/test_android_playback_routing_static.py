"""Source-only v2 routing guards, not Android execution or acoustic verification.

OwnershipHarness in test_android_audio_core.py executes production Java policy;
these checks cannot prove AudioTrack/SoundPool behavior or Kotlin compilation.
"""
from pathlib import Path

JAVA = Path(__file__).resolve().parents[2] / 'android/app/src/main/java/id/my/yhdev/nexo'


def body(text, marker):
    start = text.index(marker)
    assert "{" in text[start:].splitlines()[0], f"Expected block body: {marker}"
    opening = text.index("{", start)
    depth, end = 1, opening + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[opening + 1:end - 1]


def test_tagged_pcm_backpressure_never_runs_on_ui_dispatch():
    service = (JAVA / 'service/NexoKioskService.kt').read_text()
    audio = body(service, 'override fun onOutdoorAudio(')
    assert 'val barrier = outputBarrier' in audio
    assert 'barrier.get(1600, TimeUnit.MILLISECONDS)' in audio
    assert 'playback_barrier_timeout' in audio
    assert 'barrier.complete(false)' in audio
    assert 'token != generation || !ownership.outputAllowed(connectionId, epoch)' in audio
    assert 'player.writeChunk(audioData)' in audio
    assert 'token == generation && ownership.outputAllowed(connectionId, epoch)' in audio
    assert 'main.post' not in audio
    assert 'dispatch { player.writeChunk' not in audio
    assert 'override fun usesTaggedOutput() = true' in service
    assert not body(service, 'override fun onAudioChunkReceived(').strip()
    barrier = body(service, 'override fun onOutputControl(')
    assert barrier.index('token != generation') < barrier.index('nextCapture()')
    assert barrier.index('lifecycle.execute') < barrier.index('recorder?.stopAndJoin()')
    assert 'val recorder = audioRecorder' in barrier
    assert 'audioRecorder?.stopAndJoin()' not in barrier
    assert 'playbackReady = stopped' in barrier
    assert 'token != generation || capture != captureGeneration' in barrier


def test_old_turn_drain_cannot_reopen_new_turn_microphone():
    service = (JAVA / 'service/NexoKioskService.kt').read_text()
    assert 'private val playbackTurn = AtomicInteger(0)' in service
    output = body(service, 'private fun output(')
    assert 'playbackTurn.incrementAndGet()' in output
    ended = output.split('"tts_end" -> {', 1)[1].split('"state_change", "cognitive"', 1)[0]
    assert 'audioPlayer.finish { main.post {' in ended
    assert 'ownership.outputAllowed(id, epoch) && turn == playbackTurn.get()' in ended
    assert ended.index('audioPlayer.finish') < ended.index('playbackPending = false')
    assert 'startRecording()' not in ended
    next_turn = body(service, 'private fun maybeNextTurn()')
    assert '!turnComplete || playbackPending || !ownership.normalAllowed()' in next_turn
    assert 'now() - playbackDrainedAt < 600' in next_turn
    assert 'automatic && vadAvailable' in next_turn


def test_active_earcons_stop_before_tagged_speech_and_actual_play_is_guarded():
    earcons = (JAVA / 'audio/EarconManager.kt').read_text()
    assert 'worker.execute' in body(earcons, 'fun stop()')
    stop = body(earcons, 'private fun stopStreams()')
    for stream in ('listeningStream', 'thinkingStream'):
        assert f'soundPool?.stop({stream})' in stop
        assert f'{stream} = 0' in stop
    for marker in ('fun playListening()', 'fun playThinking()'):
        play = body(earcons, marker)
        assert play.index('outputGuard {') < play.index('soundPool.play(')
    service = (JAVA / 'service/NexoKioskService.kt').read_text()
    output = body(service, 'private fun output(')
    tts = output.split('"tts_start" -> {', 1)[1].split('"tts_end" -> {', 1)[0]
    assert tts.index('earconManager.stop()') < tts.index('audioPlayer.start()')
    assert 'if (playbackReady) audioPlayer.start()' in tts
    assert 'EarconManager(this, ::outputGuard)' in service
    guard = body(service, 'private fun outputGuard(connection:')
    assert 'synchronized(ownership)' not in service
    assert 'ownership.runOutput(connection, epoch)' in guard
    assert 'destroyed || !ownership.outputAllowed(connection, epoch)' in guard
    assert guard.index('ownership.outputAllowed') < guard.index('action()')


def test_player_checks_captured_connection_epoch_at_actual_track_creation_and_write():
    service = (JAVA / 'service/NexoKioskService.kt').read_text()
    player = body(service, 'private fun newPlayer()')
    assert 'val connection = ownership.connection()' in player
    assert 'val epoch = ownership.epoch()' in player
    assert 'playbackReady && connection != null && outputGuard(connection, epoch, action)' in player
    audio = (JAVA / 'audio/AudioPlayerManager.kt').read_text()
    start = body(audio, 'private fun startTrack()')
    assert start.index('outputFactory()') < start.index('outputGuard {') < start.index('track.start()')
    assert 'if (current(token))' in start
    assert 'if (!started) track.close()' in start
    write = body(audio, 'fun writeChunk(')
    assert 'worker.execute' in write
    assert 'outputGuard { if (current(token)) written = output?.write(bytes, offset, length)' in write
    assert 'AudioTrack.WRITE_NON_BLOCKING' in audio
    confirm = body(audio, 'fun stopAndConfirm(')
    assert 'generation.incrementAndGet()' in confirm
    assert 'worker.execute { val stopped = stopTrack(); failed = !stopped; onStopped(stopped) }' in confirm
    assert 'track.pause(); track.flush(); track.stop()' in audio
