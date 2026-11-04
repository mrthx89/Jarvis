"""Source wiring checks only. Not Android execution, compilation or race proof."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JAVA = ROOT / "android/app/src/main/java/id/my/yhdev/nexo"


def text(path):
    return (JAVA / path).read_text()


def test_open_sheet_fences_before_ui_and_queries_only_after_lease():
    activity = text("MainActivity.kt")
    opening = activity.split("private fun showVoiceprintManagement()", 1)[1].split("override fun onEnrollmentLevel", 1)[0]
    assert opening.index("openRecordingSheet(") < opening.index("VoiceprintManagementSheet(")
    service = text("service/NexoKioskService.kt")
    assert "AudioOwnership()" in service
    assert "ownership.openSheet(" in service
    assert 'putBoolean("enabled", false).commit()' in service
    assert 'sendOutdoorControl("audio_owner_acquire"' in service
    assert "ownership.localStopped(" in service
    acquire = service.split('fun openRecordingSheet(', 1)[1].split('private fun tryAcquire()', 1)[0]
    assert 'audioPlayer.stopAndConfirm { playerStopped.complete(it) }' in acquire
    assert acquire.index('ownership.openSheet(') < acquire.index('audioPlayer.stopAndConfirm')
    assert acquire.index('playerStopped.get(1500, TimeUnit.MILLISECONDS)') < acquire.index('ownership.localStopped(')
    assert 'if (!stopped || !outputStopped || !chimesStopped || !diagnosticStopped) { fail(' in acquire
    query = service.split('fun queryVoiceprint()', 1)[1].split('fun unenrollVoiceprint()', 1)[0]
    assert 'if (!ownership.dedicatedAllowed(now())' in query
    ready = service.split('if (sheetState == "ACQUIRING" && sheetLocalStopped && ownership.dedicatedAllowed(time))', 1)[1].split('if (queryRequest', 1)[0]
    assert ready.index('sheet("READY",') < ready.index('queryVoiceprint()')
    assert "sendAudioChunk(" not in service
    assert "client.startVoiceprintEnrollment(" not in service


def test_recorder_and_upload_workers_are_separate_and_explicit_outdoor():
    service = text("service/NexoKioskService.kt")
    assert '"Nexo-AudioLifecycle"' in service
    assert '"Nexo-AudioUpload"' in service
    assert "options = CaptureOptions(" in service
    assert "local.accept(token, bytes, count," in service
    assert "finishAndJoin()" in service
    assert "uploadUtterance(sealed," in service
    assert "SileroVad(" in service
    assert "local.release(sealed)" in service
    assert "automaticVad = automatic" in service


def test_actual_output_has_owner_epoch_fence_and_confirmed_stop():
    player = text("audio/AudioPlayerManager.kt")
    assert "outputGuard" in player
    assert "fun stopAndConfirm(" in player
    assert "outputGuard {" in player
    earcon = text("audio/EarconManager.kt")
    assert "outputGuard" in earcon
    assert 'worker.execute' in earcon
    assert '@Synchronized' not in earcon
    network = text("network/NexoWebSocketClient.kt")
    assert "onOutdoorAudio(" in network
    assert "onOutputControl(" in network


def test_sheet_has_explicit_finish_save_resume_and_every_exit_releases():
    sheet = text("ui/VoiceprintManagementSheet.kt")
    assert "onSave:" in sheet
    assert "onFinish:" in sheet
    assert "onSessionChanged(" in sheet
    assert "onDismiss()" in sheet
    assert "4000L" not in sheet
    assert "onResumeMicrophone()" in sheet
    activity = text("MainActivity.kt")
    assert "kioskService?.setUiForeground(false)" in activity
    opening = activity.split('private fun showVoiceprintManagement()', 1)[1].split('override fun onRecordingSessionChanged(', 1)[0]
    assert 'val service = kioskService ?: return' in opening
    assert 'onCancel = { service.cancelVoiceprintEnrollment() }' in opening
    assert 'onResumeMicrophone = { recordingResumeRequested = true; service.resumeAfterRecording() }' in opening
    dismiss = opening.split('onDismiss = {', 1)[1]
    assert 'if (!recordingResumeRequested) service.closeRecordingSheet("dismiss")' in dismiss
    assert 'startVoice(' not in dismiss
    on_stop = activity.split('override fun onStop()', 1)[1].split('private fun hideSystemUI()', 1)[0]
    assert on_stop.index('kioskService?.setUiForeground(false)') < on_stop.index('voiceprintSheet?.dismiss()')
    service = text('service/NexoKioskService.kt')
    foreground = service.split('fun setUiForeground(', 1)[1].split('fun setAutomaticCapture(', 1)[0]
    assert 'if (!value)' in foreground
    assert 'closeRecordingSheet("background")' in foreground
    assert 'pauseLocal("background")' in foreground
    close = service.split('fun closeRecordingSheet(', 1)[1].split('fun cancelVoiceprintEnrollment()', 1)[0]
    assert 'resume: Boolean = false' in close
    assert close.index('ownership.closeSheet(') < close.index('invalidateCapture(reason)') < close.index('sendOutdoorControl("audio_owner_release"')
    assert 'fun resumeAfterRecording() { closeRecordingSheet("explicit_resume", resume = true) }' in service
    release = service.split('private fun completeReleaseIfReady()', 1)[1].split('private var resumeOnHello', 1)[0]
    assert 'if (!releaseLocallyStopped || !releaseAcknowledged) return' in release
    assert 'if (resumeOnRelease && permitted())' in release


def test_output_callbacks_validate_before_mutation_and_timeout_fails_turn():
    service = text('service/NexoKioskService.kt')
    output = service.split('override fun onOutputControl(', 1)[1].split('override fun onStateChanged(', 1)[0]
    assert 'main.post {' in output
    assert output.index('token != generation') < output.index('outputBarrier = it')
    assert output.index('token != generation') < output.index('playbackReady = false')
    assert output.index('ownership.outputAllowed(id, epoch)') < output.index('nextCapture()')
    assert 'val recorder = audioRecorder' in output
    assert 'recorder?.stopAndJoin()' in output
    assert 'audioRecorder?.stopAndJoin()' not in output
    assert 'fail("Mikrofon belum berhenti sebelum jawaban")' in output
    assert 'playback_barrier_timeout' in output
    assert 'barrier.complete(false)' in output


def test_callback_failure_and_invalidation_do_not_block_main_or_stop_replacement():
    service = text('service/NexoKioskService.kt')
    failed = service.split('private fun captureFailed(', 1)[1].split('private fun invalidateCapture(', 1)[0]
    assert 'main.post {' in failed
    assert 'captureAdmission.revoke(capture)' in failed
    assert 'destroyed || revoked != captureGeneration' in failed
    assert 'captureAdmission.close()' in service
    assert 'local.fail(' not in failed
    assert 'lifecycle.execute' not in failed
    invalidate = service.split('private fun invalidateCapture(', 1)[1].split('private fun finishCapture(', 1)[0]
    assert invalidate.index('lifecycle.execute') < invalidate.index('local.cancel(reason)')
    assert 'val recorder = audioRecorder' in invalidate
    assert 'recorder?.stopAndJoin()' in invalidate
    assert 'audioRecorder?.stopAndJoin()' not in invalidate
    assert 'ownership.runOutput(connection, epoch)' in service
    assert 'synchronized(ownership)' not in service


def test_diagnostic_revocation_is_atomic_and_callbacks_own_their_player():
    diagnostic = text('audio/PrivateAudioDiagnostic.kt')
    assert 'AtomicLong' in diagnostic
    assert 'synchronized(playbackFence)' not in diagnostic
    assert 'stopPlayback(current)' in diagnostic
    assert 'if (player !== expected) return' in diagnostic
    player = text('audio/AudioPlayerManager.kt')
    assert 'fun start()' in player.split('internal interface PcmOutput', 1)[1].split('private fun createPcmOutput', 1)[0]
    start = player.split('private fun startTrack()', 1)[1].split('private fun current(', 1)[0]
    assert start.index('outputFactory()') < start.index('outputGuard {') < start.index('track.start()')
    network = text('network/NexoWebSocketClient.kt')
    cancel = network.split('fun cancelUtterance()', 1)[1].split('fun fenceOutput()', 1)[0]
    assert 'uploadWorker.execute' in cancel
    disconnect = network.split('fun disconnect()', 1)[1]
    assert disconnect.index('uploadWorker.execute') < disconnect.index('uploader.cancel(')
    assert 'uploadWorker.shutdown()' in disconnect
    assert 'uploadWorker.shutdownNow()' not in disconnect


def test_save_refreshes_authoritative_profile_without_inventing_local_status():
    service = text('service/NexoKioskService.kt')
    saved = service.split('"saved" -> if (enrollmentOperation == "save")', 1)[1].split('"rejected", "cancelled"', 1)[0]
    assert saved.index('sheet("SAVED",') < saved.index('querySent = false') < saved.index('queryVoiceprint()')
    assert 'isEnrolled = true' not in saved


def test_accepted_upload_has_bounded_terminal_result_and_fail_closed_cleanup():
    service = text('service/NexoKioskService.kt')
    assert 'private val processing = ProcessingDeadline()' in service
    start = service.split('pendingUtterance = sealed.id', 1)[1].split('upload.execute', 1)[0]
    assert 'processing.begin(sealed.id)' in start
    ack = service.split('"utterance_ack" -> {', 1)[1].split('"utterance_result" -> {', 1)[0]
    assert 'processing.accepted(pendingUtterance!!, now())' in ack
    assert 'setOf("ACCEPTED", "COMPLETED")' in ack
    result = service.split('"utterance_result" -> {', 1)[1].split('"utterance_error" ->', 1)[0]
    assert result.index('processing.complete(utterance, now())') < result.index('samples.add(utterance)')
    assert 'fail("Hasil ucapan terlambat atau belum dikonfirmasi")' in result
    assert 'code in setOf("stt_empty", "no_speech", "speaker_rejected", "quality_insufficient")' in result
    tick = service.split('private val tick =', 1)[1]
    assert 'if (processing.expired(time)) fail(' in tick
    invalidate = service.split('private fun invalidateCapture(', 1)[1].split('private fun finishCapture(', 1)[0]
    assert 'processing.clear()' in invalidate
    failed = service.split('private fun fail(', 1)[1].split('fun initializeConnection(', 1)[0]
    assert 'closeRecordingSheet("error")' in failed
    assert 'pauseLocal("error")' in failed


def test_earcon_stop_is_worker_only_and_acquire_waits_for_confirmation():
    earcon = text('audio/EarconManager.kt')
    stop = earcon.split('fun stop()', 1)[1].split('fun release()', 1)[0]
    assert 'worker.execute' in stop
    assert 'fun stopAndConfirm(' in earcon
    service = text('service/NexoKioskService.kt')
    acquire = service.split('fun openRecordingSheet(', 1)[1].split('private fun tryAcquire()', 1)[0]
    assert 'val earconsStopped = earconManager.stopAndConfirm()' in acquire
    assert 'earconsStopped.get(' in acquire
    assert '!chimesStopped' in acquire
    network = text('network/NexoWebSocketClient.kt')
    upload = network.split('fun uploadUtterance(', 1)[1].split('fun cancelUtterance()', 1)[0]
    assert upload.index('uploadWorker.execute') < upload.index('uploader.begin(')
    assert 'stillCurrent()' in upload
    assert 'setExecuteExistingDelayedTasksAfterShutdownPolicy(false)' in network
    assert 'setContinueExistingPeriodicTasksAfterShutdownPolicy(false)' in network
