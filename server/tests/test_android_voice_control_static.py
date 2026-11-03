"""Source-only v2 voice/privacy wiring; not Android runtime or build evidence.

SDK-free ownership transitions execute separately in test_android_audio_core.py's
OwnershipHarness. Neither those host checks nor these guards verify Android FGS,
microphone hardware, scheduling, acoustic silence, or Kotlin compilation.
"""
from pathlib import Path


SERVICE = (Path(__file__).resolve().parents[2] /
           "android/app/src/main/java/id/my/yhdev/nexo/service/NexoKioskService.kt")


def source():
    return SERVICE.read_text()


def body(marker):
    text = source()
    start = text.index(marker)
    # Do not accidentally treat the next function as an expression-bodied method.
    line = text[start:].splitlines()[0]
    assert "{" in line, f"Expected block body: {marker}"
    opening = text.index("{", start)
    depth, end = 1, opening + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[opening + 1:end - 1]


def test_voice_choice_is_private_persisted_default_off_and_public_read_only():
    text = source()
    assert "val isVoiceEnabled: Boolean get() = voiceEnabled" in text
    assert 'getSharedPreferences("voice_control", Context.MODE_PRIVATE)' in text
    assert "private var voiceEnabled = false" in text
    created = body("override fun onCreate()")
    assert created.index("persistOff()") < created.index("startForegroundNotification()")
    assert 'getBoolean("enabled"' not in text  # Saved ON cannot authorize a restart.
    off = body("private fun persistOff()")
    for fragment in ('voiceEnabled = false', 'putBoolean("enabled", false).commit()',
                     'onVoiceEnabledChanged(false)'):
        assert fragment in off
    setter = body("set(value)")
    assert setter.index("onVoiceEnabledChanged(voiceEnabled)") < setter.index("onStateChanged(")
    assert "onRecordingSessionChanged(session())" in setter
    start = body("fun startVoice()")
    assert "!permitted() || ownership.owner() != null || sheetOwner != null" in start
    assert "resumeOnHello = true" in start and "requestResume()" in start
    resume = body("private fun requestResume()")
    assert resume.index("ownership.resume(request, now())") < resume.index('putBoolean("enabled", true).commit()')
    assert 'sendOutdoorControl("conversation_resume", request)' in resume
    assert "mikrofon belum aktif" in resume
    assert "startRecording()" not in start + resume


def test_endpoint_and_credential_changes_pause_without_reading_tokens_or_connecting():
    initialize = body("fun initializeConnection(")
    assert "ServerEndpointPolicy.isValid(serverUrl)" in initialize
    assert 'closeRecordingSheet("endpoint_changed")' in initialize
    assert 'pauseLocal("endpoint_changed")' in initialize
    assert "connectionKey = Pair(serverUrl, deviceId)" in initialize
    refresh = body("fun refreshCredentials()")
    assert 'closeRecordingSheet("credentials_changed")' in refresh
    assert 'pauseLocal("credentials_changed")' in refresh
    for forbidden in ("credentialStore.read", "connect()", "ensureConnection()", "voiceEnabled = true"):
        assert forbidden not in initialize + refresh
    # Only an explicit start/sheet open creates an authenticated control socket.
    connection = body("private fun ensureConnection()")
    assert connection.index("credentialStore.read(key.first, key.second)") < connection.index("NexoWebSocketClient(")
    assert "bearer, events" in connection
    assert "isVoiceEnabled" not in connection  # Dedicated recording works normal OFF.


def test_stop_closes_send_and_output_gates_before_teardown_and_publishes_paused():
    stop = body("fun stopVoice()")
    assert stop.index('closeRecordingSheet("STOP")') < stop.index('pauseLocal("STOP")') < stop.index("stopSelf()")
    assert "resumeOnHello = false; resumeOnRelease = false" in stop
    pause = body("private fun pauseLocal(")
    ordered = ["webSocketClient?.fenceCapture()", "persistOff()", "ownership.disconnected()",
               "webSocketClient?.fenceOutput()", "invalidateCapture(reason)",
               "earconManager.stop()", "audioPlayer.stop()", "++generation",
               "webSocketClient?.disconnect()", "webSocketClient = null", "publish(NexoState.IDLE"]
    positions = [pause.index(fragment) for fragment in ordered]
    assert positions == sorted(positions)
    assert "Paused" in pause and "connectionKey = null" not in pause
    assert "NexoState.ERROR" not in stop + pause


def test_capture_errors_invalidate_and_pause_instead_of_automatic_recovery():
    capture = body("private fun startCapture(")
    assert "val gate = ownership.generation()" in capture
    assert "val capture = nextCapture()" in capture
    assert "val socket = generation" in capture
    assert "onError = { code -> captureFailed(capture, code) }" in capture
    assert "onRecoverableError = { code -> captureFailed(capture, code) }" in capture
    assert 'onRouteInvalidated = { captureFailed(capture, "route_changed") }' in capture
    failure = body("private fun captureFailed(")
    assert failure.index("captureAdmission.revoke(capture)") < failure.index("main.post {")
    assert "destroyed || revoked != captureGeneration" in failure
    assert "local.fail(" not in failure
    assert "lifecycle.execute" not in failure
    assert 'fail("Rekaman gagal: $code")' in failure
    invalidation = body("private fun invalidateCapture(")
    assert "recorder?.stopAndJoin()" in invalidation
    assert invalidation.index("lifecycle.execute") < invalidation.index("local.cancel(reason)")
    fail = body("private fun fail(")
    assert 'closeRecordingSheet("error")' in fail and 'pauseLocal("error")' in fail
    assert "resumeOnHello = false; resumeOnRelease = false" in fail
    for forbidden in ("ensureConnection()", "requestResume()", "postDelayed", "startRecording()"):
        assert forbidden not in failure + fail


def test_socket_events_and_playback_completion_use_generation_connection_and_epoch():
    connection = body("private fun ensureConnection()")
    assert "if (!destroyed && token == generation) action()" in connection
    assert "override fun usesTaggedOutput() = true" in connection
    for marker in ("override fun onConnected()", "override fun onTelemetryReceived(",
                   "override fun onTranscriptionReceived(", "override fun onAudioChunkReceived(",
                   "override fun onTTSStarted()", "override fun onTTSEnded()"):
        assert not body(marker).strip(), marker  # Untagged v1 callbacks are inert.
    assert 'override fun onDisconnected() = dispatch { transientDisconnect() }' in connection
    transient = body("private fun transientDisconnect()")
    assert "ownership.disconnected()" in transient and "fenceOutput()" in transient
    assert "requestResume()" not in transient and "startRecording()" not in transient
    assert "recovery.clear()" in body("private fun invalidateCapture(")
    output = body("private fun output(")
    assert 'OutdoorProtocol.uuid(json, "connection_id") ?: return' in output
    assert 'OutdoorProtocol.integer(json, "output_epoch", 0, Long.MAX_VALUE) ?: return' in output
    assert "if (!ownership.outputAllowed(id, epoch)) return" in output
    assert "ownership.outputAllowed(id, epoch) && turn == playbackTurn.get()" in output
    resume = body("private fun startResumedRecorder(")
    ready = "ownership.recorderReady(gate, started && recorder.isRecording())"
    assert resume.index(ready) < resume.index("allowOutput(id, epoch)") < resume.index("publish(NexoState.LISTENING")


def test_playback_keeps_speaking_expression_until_current_turn_drains():
    output = body("private fun output(")
    started = output.split('"tts_start" -> {', 1)[1].split('"tts_end" -> {', 1)[0]
    assert 'publish(NexoState.SPEAKING, "Menjawab • mikrofon dijeda", currentExpression)' in started
    ended = output.split('"tts_end" -> {', 1)[1].split('"state_change", "cognitive"', 1)[0]
    assert ended.index("audioPlayer.finish") < ended.index("playbackPending = false") < ended.index("maybeNextTurn()")
    next_turn = body("private fun maybeNextTurn()")
    assert "!turnComplete || playbackPending || !ownership.normalAllowed()" in next_turn
    assert "now() - playbackDrainedAt < 600" in next_turn
    assert "sleepExpression" in next_turn


def test_notification_does_not_claim_capture_and_destroy_persists_paused():
    notification = body("private fun startForegroundNotification()")
    assert "lihat layar untuk status mikrofon" in notification
    for claim in ("Mikrofon aktif", "Layanan suara aktif", "Merekam"):
        assert claim not in notification
    destroy = body("override fun onDestroy()")
    for fragment in ('closeRecordingSheet("service_destroy")', "destroyed = true; ++generation; persistOff()",
                     "ownership.disconnected()", "fenceOutput()", 'invalidateCapture("service_destroy")',
                     "removeCallbacksAndMessages(null)", "webSocketClient?.disconnect()"):
        assert fragment in destroy
    assert "lifecycle.execute" in destroy
    assert destroy.index("val joined = audioRecorder?.stopAndJoin()") < destroy.index("if (joined) { vad?.close()")


def test_nexo_voice_pipeline_constants():
    """Configuration check only, not STT/TTS or microphone execution."""
    from nexo_server.config import STTSettings, TTSSettings
    stt = STTSettings()
    tts = TTSSettings()
    assert stt.language == "id"
    assert "id-ID" in tts.voice
