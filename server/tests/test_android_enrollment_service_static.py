"""Source-contract checks only: no Android build or device required."""
from pathlib import Path

SERVICE = (Path(__file__).resolve().parents[2] /
           "android/app/src/main/java/id/my/yhdev/nexo/service/NexoKioskService.kt")


def source():
    return SERVICE.read_text()


def body(signature):
    text = source()
    start = text.rindex(signature)
    opening = text.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[opening + 1:end - 1]


def test_dedicated_enrollment_routes_pcm_only_after_recording_ack():
    text = source()
    assert "fun startVoiceprintEnrollment(): Boolean" in text
    start = body("fun startVoiceprintEnrollment(): Boolean")
    for guard in ("isVoiceEnabled", "foregroundAllowed", "RECORD_AUDIO",
                  "duplex.canSendAudio", "NexoState.THINKING", "NexoState.SPEAKING"):
        assert guard in start
    assert start.index("pendingEnrollmentId =") < start.index("client.startVoiceprintEnrollment(")
    capture = body("private fun createAudioSession()")
    assert "synchronized(enrollmentLock)" in capture
    assert "EnrollmentPhase.RECORDING" in capture
    assert capture.index("pendingEnrollmentId") < capture.index("duplex.canSendAudio")
    assert "onEnrollmentLevel" in capture
    assert "if (localAmp > 0.05f)" not in capture  # silence must reset the meter


def test_finish_cancel_and_replies_are_request_and_generation_fenced():
    finish = body("fun finishVoiceprintEnrollment(): Boolean")
    cancel = body("fun cancelVoiceprintEnrollment()")
    for control, phase, send in ((finish, "SAVING", "finishVoiceprintEnrollment"),
                                  (cancel, "CANCELLING", "cancelVoiceprintEnrollment")):
        assert "synchronized(enrollmentLock)" in control
        assert control.index("EnrollmentPhase." + phase) < control.index("?." + send + "(")
        assert "pendingEnrollmentId = null" not in control
    reply = body("override fun onVoiceprintDialogReceived(dialog:")
    assert "dialog.requestId != pendingEnrollmentId" in reply
    assert "enrollmentGeneration != generation" in reply
    assert '"recording"' in reply and "EnrollmentPhase.STARTING" in reply
    assert "EnrollmentPhase.CANCELLING" in reply and '"cancelled"' in reply
    assert 'dialog.action == "enrolled" && enrollmentPhase != EnrollmentPhase.SAVING' in reply
    assert "queryVoiceprint()" in reply
    assert "startRecording()" not in reply
    timeout = body("private fun armEnrollmentTimeout(")
    assert "cancelVoiceprintEnrollment()" in timeout
    assert "failAudioSession(" in timeout


def test_enrollment_suppresses_conversation_and_invalidates_on_session_end():
    for signature in ("private fun publishListening()", "override fun onTelemetryReceived(",
                      "override fun onTranscriptionReceived(", "override fun onAudioChunkReceived(",
                      "override fun onTTSStarted()", "override fun onTTSEnded()"):
        assert "enrollmentActive" in body(signature), signature
    state = body("override fun onStateChanged(state:")
    assert "enrollmentActive" in state and "invalidateVoiceprint(" in state
    for signature in ("fun stopVoice()", "private fun failAudioSession(",
                      "fun initializeConnection(", "private fun foregroundDenied()",
                      "override fun onDisconnected()", "override fun onDestroy()"):
        assert "invalidateVoiceprint(" in body(signature), signature
    invalidation = body("private fun invalidateVoiceprint(")
    assert "available = null" in invalidation
    assert "lastVoiceprintDialog = null" in invalidation
    output = body("override fun onAudioChunkReceived(")
    assert 'failAudioSession("' in output
    assert "chunk dropped" not in output


def test_level_callback_is_optional_and_posted_to_main_thread():
    assert "fun onEnrollmentLevel(amplitude: Float) {}" in source()
    capture = body("private fun createAudioSession()")
    assert capture.index("main.post {") < capture.index("serviceCallback?.onEnrollmentLevel(localAmp)")
    terminal = body("private fun completeEnrollment(")
    assert "enrollmentPhase = null" in terminal
    assert "startRecording()" not in terminal
    assert "onEnrollmentLevel(0f)" in terminal
    assert "onTelemetryReceived(0f, null)" in terminal


def test_start_silences_existing_chime_and_failed_controls_fence_deferred_errors():
    start = body("fun startVoiceprintEnrollment(): Boolean")
    assert "earconManager.release()" in start
    for signature in ("fun startVoiceprintEnrollment(): Boolean",
                      "fun finishVoiceprintEnrollment(): Boolean",
                      "fun cancelVoiceprintEnrollment()"):
        control = body(signature)
        assert "val token = generation" in control
        assert "token == generation" in control
    assert "earconManager = EarconManager(this)" in body("private fun completeEnrollment(")
    assert "NexoState.LISTENING" in start or "NexoState.IDLE" in start


def test_sample_tail_never_returns_to_conversation_and_resume_is_explicit():
    for signature in ("fun finishVoiceprintEnrollment(): Boolean", "fun cancelVoiceprintEnrollment()"):
        control = body(signature)
        assert "pauseEnrollmentCapture()" in control
    pause = body("private fun pauseEnrollmentCapture()")
    assert "audioRecorder.stopRecording()" in pause
    assert "duplex.microphoneReady(false)" in pause
    complete = body("private fun completeEnrollment(")
    assert "pauseEnrollmentCapture()" in complete
    assert "startRecording()" not in complete
    # Existing voice-enabled state must not make an explicit resume a no-op.
    assert "audioRecorder.isRecording()" in body("fun startVoice()")
