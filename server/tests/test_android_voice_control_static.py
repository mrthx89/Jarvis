"""Source-only voice/privacy guards; no Kotlin compilation or device execution.

These checks inspect service wiring, not actual Android microphone/FGS behavior.
"""
from pathlib import Path
import re


def _get_service_file():
    candidates = [
        'android/app/src/main/java/id/my/yhdev/nexo/service/NexoKioskService.kt',
        'android/app/src/main/java/id/my/yhdev/nexo/service/NexoKioskService.kt',
    ]
    for c in candidates:
        p = Path(__file__).resolve().parents[2] / c
        if p.exists():
            return p
    raise FileNotFoundError(f"None found from {candidates}")


SERVICE = _get_service_file()


def source():
    return SERVICE.read_text()


def body(marker):
    text = source()
    start = text.index('{', text.index(marker))
    depth = 1
    end = start + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start + 1:end - 1]


def test_voice_choice_is_private_persisted_default_off_and_public_read_only():
    text = source()
    assert 'val isVoiceEnabled: Boolean' in text
    assert 'get() = voiceEnabled' in text
    assert 'getSharedPreferences("voice_control", Context.MODE_PRIVATE)' in text
    created = body('override fun onCreate()')
    assert 'getBoolean("enabled", false)' in created
    assert 'if (isVoiceEnabled)' in created
    assert (
        'publish(NexoState.DISCONNECTED, "Menunggu endpoint server...")' in created or
        'publish(NexoState.DISCONNECTED, "Menunggu endpoint server...")' in created
    )
    assert 'fun startVoice()' in text
    assert 'fun stopVoice()' in text
    assert 'fun onVoiceEnabledChanged(enabled: Boolean)' in text
    setter = body('set(value)')
    assert 'value?.onVoiceEnabledChanged(isVoiceEnabled)' in setter
    assert 'value?.onStateChanged(currentState, currentMessage, currentExpression)' in setter
    assert setter.index('onVoiceEnabledChanged') < setter.index('onStateChanged')
    assert 'voicePreferences.edit().putBoolean("enabled", true).commit()' in body('fun startVoice()')
    assert 'voicePreferences.edit().putBoolean("enabled", false).commit()' in body('fun stopVoice()')


def test_stopped_endpoint_and_credential_refresh_cannot_read_tokens_or_connect():
    initialize = body('fun initializeConnection(')
    saved = initialize.index('connectionKey = Pair(serverUrl, deviceId)')
    guard = initialize.index('if (!isVoiceEnabled)')
    read = initialize.index('credentialStore.read(serverUrl, deviceId)')
    connect = initialize.index('webSocketClient?.connect()')
    assert saved < guard < read < connect
    assert 'return' in initialize[guard:read]
    assert 'voiceEnabled = true' not in initialize
    refresh = body('fun refreshCredentials()')
    assert 'if (!isVoiceEnabled) return' in refresh
    assert 'forceReconnect = true' in refresh
    assert 'forceReconnect = true' in body('fun startVoice()')


def test_stop_persists_before_teardown_fences_callbacks_and_publishes_paused_idle():
    stop = body('fun stopVoice()')
    publish_idle = 'publish(NexoState.IDLE' if 'publish(NexoState.IDLE' in stop else 'publish(NexoState.IDLE'
    ordered = ['putBoolean("enabled", false).commit()', 'voiceEnabled = false',
               '++generation', '++audioGeneration', 'duplex.disconnected()',
               'audioRecorder.stopRecording()', 'audioPlayer.stop()',
               'webSocketClient?.disconnect()', 'webSocketClient = null',
               'onVoiceEnabledChanged(false)', publish_idle,
               'startForegroundNotification()']
    positions = [stop.index(fragment) for fragment in ordered]
    assert positions == sorted(positions)
    assert 'dijeda' in stop
    assert 'NexoState.ERROR' not in stop and 'NexoState.ERROR' not in stop
    assert 'menghubungkan ulang' not in stop
    assert 'connectionKey = null' not in stop


def test_audio_sessions_capture_generation_and_never_reconnect_on_audio_failure():
    session = body('private fun createAudioSession()')
    assert 'val audioToken = ++audioGeneration' in session
    assert session.count('if (!isVoiceEnabled || audioToken != audioGeneration) return@post') == 2
    assert 'isVoiceEnabled && audioToken == audioGeneration && duplex.canSendAudio' in session
    assert 'if (isVoiceEnabled && token == generation && audioToken == audioGeneration)' in session
    assert 'initializeConnection(' not in session
    fail = body('private fun failAudioSession(')
    assert '++generation' in fail
    assert '++audioGeneration' in fail
    assert 'duplex.disconnected()' in fail
    assert 'audioRecorder.stopRecording()' in fail
    assert 'audioPlayer.stop()' in fail
    assert 'webSocketClient?.disconnect()' in fail
    assert 'webSocketClient = null' in fail
    assert 'voiceEnabled = false' not in fail  # STOP remains available after permission/output error.
    assert 'menghubungkan ulang' not in fail
    connected = body('override fun onConnected() {')
    assert connected.index('if (!isVoiceEnabled) return') < connected.index('audioRecorder.startRecording()')
    assert connected.index('createAudioSession()') < connected.index('audioRecorder.startRecording()')
    assert source().count('audioRecorder.startRecording()') == 1


def test_socket_events_and_playback_completion_are_fenced_while_stopped():
    initialize = body('fun initializeConnection(')
    assert 'if (isVoiceEnabled && token == generation) action()' in initialize
    for method in ('onConnected()', 'onDisconnected()',
                   'onStateChanged(state:', 'onTelemetryReceived(amplitude:',
                   'onTranscriptionReceived(role:', 'onAudioChunkReceived(audioData:',
                   'onTTSStarted()', 'onTTSEnded()'):
        # Match concrete service overrides, not the forwarding listener expressions.
        text = source()
        pattern = r'override fun ' + re.escape(method) + r'[^\n]*\{'
        matches = list(re.finditer(pattern, text))
        assert matches, method
        implementation = body(matches[-1].group())
        assert 'if (!isVoiceEnabled) return' in implementation, method
    ended = body('override fun onTTSEnded() {')
    assert 'if (isVoiceEnabled && token == generation && audioToken == audioGeneration)' in ended
    assert 'if (isVoiceEnabled && duplex.canSendAudio)' in body('private fun publishListening()')
    disconnected = body('override fun onDisconnected() {')
    assert '++audioGeneration' in disconnected
    assert 'audioRecorder.stopRecording()' in disconnected
    assert 'audioPlayer.stop()' in disconnected


def test_playback_events_preserve_speaking_expression_until_audio_drains():
    started = body('override fun onTTSStarted() {')
    assert 'currentExpression)' in started
    text = source()
    changed_pattern = r'override fun onStateChanged\(state:\s*(?:NexoState|NexoState),\s*message:\s*String\?,\s*expression:\s*String\?\)\s*\{'
    match = re.search(changed_pattern, text)
    assert match is not None
    changed = body(match.group())
    assert 'mikrofon dijeda", currentExpression)' in changed


def test_notification_is_truthful_about_paused_and_actual_capture():
    notification = body('private fun startForegroundNotification()')
    assert '!isVoiceEnabled' in notification
    assert 'Mikrofon nonaktif' in notification
    assert 'audioRecorder.isRecording()' in notification
    assert 'Mikrofon aktif' in notification
    assert 'mikrofon belum aktif' in notification
    assert 'Layanan suara aktif' not in notification
    assert 'startForegroundNotification()' in body('private fun publish(')
    destroy = body('override fun onDestroy()')
    assert 'voiceEnabled = false' in destroy
    assert '++audioGeneration' in destroy
    assert 'putBoolean' not in destroy  # Service destruction must not erase user choice.


def test_nexo_voice_pipeline_constants():
    """Verify Nexo STT/TTS and audio constants match voice session contract."""
    from nexo_server.config import STTSettings, TTSSettings
    stt = STTSettings()
    tts = TTSSettings()
    assert stt.language == "id"
    assert "id-ID" in tts.voice
