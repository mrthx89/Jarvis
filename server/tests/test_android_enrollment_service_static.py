"""Source-only v2 enrollment wiring, not Android compilation/runtime evidence.

OwnershipHarness in test_android_audio_core.py executes the SDK-free production
AudioOwnership policy (normal OFF, lease/barrier, stale grants/resume, expiry).
These guards only inspect its Kotlin wiring; hardware/privacy acceptance is pending.
"""
from pathlib import Path

SERVICE = (Path(__file__).resolve().parents[2] /
           "android/app/src/main/java/id/my/yhdev/nexo/service/NexoKioskService.kt")


def source():
    return SERVICE.read_text()


def body(signature):
    text = source()
    start = text.index(signature)
    assert "{" in text[start:].splitlines()[0], f"Expected block body: {signature}"
    opening = text.index("{", start)
    depth, end = 1, opening + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[opening + 1:end - 1]


def test_dedicated_enrollment_works_normal_off_after_local_stop_and_owner_ack():
    opening = body("fun openRecordingSheet(")
    ordered = ["webSocketClient?.fenceCapture()", "persistOff()", "ownership.openSheet(",
               "webSocketClient?.fenceOutput()", 'invalidateCapture("sheet_open")',
               "earconManager.stop()", 'sheet("ACQUIRING"', "audioPlayer.stopAndConfirm"]
    positions = [opening.index(fragment) for fragment in ordered]
    assert positions == sorted(positions)
    assert "lifecycle.execute" in opening and "recorder?.stopAndJoin()" in opening
    assert "val recorder = audioRecorder" in opening
    assert "audioRecorder?.stopAndJoin()" not in opening
    assert "playerStopped.get(1500, TimeUnit.MILLISECONDS)" in opening
    assert "if (!stopped || !outputStopped || !chimesStopped || !diagnosticStopped)" in opening
    assert "ownership.localStopped(ownership.generation(), now())" in opening
    assert "ensureConnection()" in opening
    acquire = body("private fun tryAcquire()")
    assert "ownership.acquire(request, now())" in acquire
    assert 'sendOutdoorControl("audio_owner_acquire", request' in acquire
    assert '"policy" to "cancel_active"' in acquire
    start = body("fun startVoiceprintEnrollment(): Boolean")
    assert "!permitted() || !ownership.dedicatedAllowed(now())" in start
    assert 'sheetPurpose != "enrollment"' in start
    assert 'sheetState !in setOf("READY", "SAMPLES_READY")' in start
    assert 'sendEnrollment("enrollment_begin", enrollmentRequest!!)' in start
    assert 'startCapture("enrollment", false)' in start
    for forbidden in ("isVoiceEnabled", "voiceEnabled", "normalAllowed", "duplex.canSendAudio"):
        assert forbidden not in opening + acquire + start
    tick = body("override fun run()")
    assert 'sheetState == "ACQUIRING" && sheetLocalStopped && ownership.dedicatedAllowed(time)' in tick
    assert tick.index("ownership.dedicatedAllowed(time)") < tick.index('sheet("READY"')


def test_finish_save_cancel_and_replies_are_request_owner_lease_connection_fenced():
    assert "fun finishVoiceprintEnrollment(): Boolean = finishCapture()" in source()
    finish = body("private fun finishCapture()")
    assert "finishPending || startingCapture || audioRecorder?.isRecording() != true" in finish
    assert finish.index("audioRecorder?.finishAndJoin() == true") < finish.index("local.sealed()")
    assert "if (capture != captureGeneration) return@post" in finish
    assert 'if (!joined || sealed == null) { fail(' in finish
    assert 'closeRecordingSheet("cancel")' in body("fun cancelVoiceprintEnrollment()")
    save = body("fun saveVoiceprintEnrollment(): Boolean")
    assert "!ownership.dedicatedAllowed(now())" in save
    assert "sampleCount < 2 || samples.size != sampleCount + 1" in save
    assert 'pendingUtterance != null || sheetState != "SAMPLES_READY"' in save
    assert save.index('enrollmentOperation = "save"') < save.index('sendEnrollment("enrollment_save"')
    assert '"holdout_utterance_id" to samples.last()' in save
    send = body("private fun sendEnrollment(")
    for field in ('"owner_id" to (sheetOwner ?: return false)',
                  '"lease_id" to (ownership.lease() ?: return false)',
                  '"enrollment_id" to (enrollmentId ?: return false)'):
        assert field in send
    assert 'if (!sent) fail(' in send
    control = body("private fun control(")
    assert 'if (id != ownership.connection()) return' in control
    assert 'OutdoorProtocol.uuid(json, "request_id") ?: return' in control
    ack = control.split('"enrollment_ack" -> {', 1)[1].split('"utterance_ack" -> {', 1)[0]
    for fence in ('!ownership.dedicatedAllowed(now())', 'request != enrollmentRequest',
                  'OutdoorProtocol.uuid(json, "owner_id") != sheetOwner',
                  'OutdoorProtocol.uuid(json, "lease_id") != ownership.lease()',
                  'OutdoorProtocol.uuid(json, "enrollment_id") != enrollmentId'):
        assert fence in ack
    assert 'enrollmentOperation == "begin"' in ack and 'enrollmentOperation == "save"' in ack
    assert '"required_samples", 2, 5' in ack and '"required_holdouts", 1, 1' in ack
    assert "Percakapan tetap Paused" in ack
    assert "startRecording()" not in ack and "requestResume()" not in ack
    reply = body("private fun voiceprint(")
    assert "sheetOwner == null || !ownership.dedicatedAllowed(now())" in reply
    assert "dialog.requestId != queryRequest && dialog.requestId != pendingDelete" in reply
    assert "dialog.requestId == null" in reply


def test_enrollment_exits_cancel_release_and_invalidate_without_auto_resume():
    for signature, reason in (("fun stopVoice()", "STOP"), ("private fun fail(", "error"),
                              ("override fun onDestroy()", "service_destroy"),
                              ("fun setUiForeground(", "background"),
                              ("fun initializeConnection(", "endpoint_changed"),
                              ("fun refreshCredentials()", "credentials_changed")):
        assert f'closeRecordingSheet("{reason}")' in body(signature), signature
    close = body("fun closeRecordingSheet(")
    assert "resume: Boolean = false" in source().split("fun closeRecordingSheet(", 1)[1].split("{", 1)[0]
    assert "resumeOnRelease = resume && permitted()" in close
    for fragment in ("persistOff()", "ownership.closeSheet(request, now())", "webSocketClient?.fenceOutput()",
                     "invalidateCapture(reason)", "diagnostic.dismiss()", "audioRecorder?.stopAndJoin()",
                     'sendOutdoorControl("enrollment_cancel"', 'sendOutdoorControl("audio_owner_release"',
                     "enrollmentId = null; enrollmentOperation = null; pendingDelete = null; queryRequest = null",
                     "samples.clear(); sampleCount = 0", 'pauseLocal("release_unconfirmed")'):
        assert fragment in close
    assert "requestResume()" not in close
    tick = body("override fun run()")
    for failure in ("time >= releaseDeadline", "time - sheetOpened >= 3000", "ownership.expired(time)",
                    "time >= enrollmentDeadline", "!permitted()"):
        assert failure in tick
    assert '"denied", "expired" -> fail(' in body("private fun control(")
    assert 'override fun onDisconnected() = dispatch { fail(' in source()


def test_level_callback_is_optional_main_tick_only_and_reset_on_invalidation():
    text = source()
    assert "fun onEnrollmentLevel(amplitude: Float) {}" in text
    assert "main.post(tick)" in body("override fun onCreate()")
    tick = body("override fun run()")
    assert "audioRecorder?.isRecording() == true && !finishPending && !startingCapture" in tick
    assert "if (sheetOwner != null) serviceCallback?.onEnrollmentLevel(rms)" in tick
    assert "local.metrics().rms()" in tick
    assert "main.postDelayed(this, 100)" in tick
    assert "0.05f" not in tick  # Signal level is not a silence gate or quality verdict.
    assert "onEnrollmentLevel" not in body("private fun startCapture(")
    invalidation = body("private fun invalidateCapture(")
    assert "onEnrollmentLevel(0f)" in invalidation
    assert "onTelemetryReceived(0f, null)" in invalidation
    assert "level sinyal bukan bukti kualitas" in body("private fun control(")


def test_sheet_silences_chime_and_failed_controls_or_capture_fail_closed():
    opening = body("fun openRecordingSheet(")
    assert opening.index("ownership.openSheet(") < opening.index("earconManager.stop()") < opening.index('sheet("ACQUIRING"')
    assert 'if (!stopped || !outputStopped || !chimesStopped || !diagnosticStopped) { fail(' in opening
    capture = body("private fun startCapture(")
    assert "purpose != \"conversation\" && !ownership.dedicatedAllowed(now())" in capture
    assert "capture != captureGeneration || gate != ownership.generation() || !permitted()" in capture
    assert "local.cancel(\"start_invalidated\"); recorder.stopAndJoin()" in capture
    assert capture.index("val started = recorder.startRecording()") < capture.index("!started || !recorder.isRecording()") < capture.index('sheet("RECORDING"')
    assert "socket != generation" in capture
    assert "if (!sent) fail(" in body("private fun sendEnrollment(")
    acquire = body("private fun tryAcquire()")
    assert 'fail("Permintaan kepemilikan mikrofon gagal")' in acquire
    assert "earconManager.play" not in body("private fun control(")


def test_sample_tail_stays_dedicated_and_resume_requires_release_ack_and_hardware():
    deliver = body("private fun deliver(")
    assert 'val lease = if (purpose == "enrollment") ownership.lease() else null' in deliver
    assert "client.uploadUtterance(sealed, lease, enrollment)" in deliver
    assert "capture == captureGeneration && !destroyed" in deliver
    assert "sendAudioChunk" not in source()  # No raw v1 fallback or read-tail routing.
    assert 'closeRecordingSheet("explicit_resume", resume = true)' in body("fun resumeAfterRecording()")
    complete = body("private fun completeReleaseIfReady()")
    assert "if (!releaseLocallyStopped || !releaseAcknowledged) return" in complete
    assert "resumeOnRelease && permitted()" in complete
    assert "requestResume()" in complete
    control = body("private fun control(")
    resumed = control.split('"conversation_resumed" -> {', 1)[1].split('"enrollment_ack"', 1)[0]
    assert "!ownership.resumed(id, request, paused, epoch) || !permitted()" in resumed
    assert "startResumedRecorder(id, epoch)" in resumed
    recorder = body("private fun startResumedRecorder(")
    assert "capture != captureGeneration || gate != ownership.generation()" in recorder
    assert recorder.index("recorder.startRecording()") < recorder.index("ownership.recorderReady(gate, started && recorder.isRecording())") < recorder.index("allowOutput(id, epoch)")
    assert '"saved" ->' in control and 'sheet("SAVED"' in control
    assert "requestResume()" not in control  # Save/release/IDLE never grants normal capture.
