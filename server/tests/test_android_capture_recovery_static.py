"""Source contracts only: no Android compilation, timing, or hardware claims.

Run with python -m unittest discover -s server/tests -p test_android_capture_recovery_static.py.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
JAVA = ROOT / "android/app/src/main/java/id/my/yhdev/nexo"


def source(relative):
    return (JAVA / relative).read_text()


def body(text, marker):
    start = text.index("{", text.index(marker))
    depth, end = 1, start + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start + 1:end - 1]


class CaptureRecoveryStaticTests(unittest.TestCase):
    def test_gate_reads_and_transitions_share_monitor(self):
        gate = source("audio/HalfDuplexGate.kt")
        self.assertRegex(gate, r"val canSendAudio: Boolean\s+@Synchronized get")
        for method in ("connected", "disconnected", "microphoneReady", "stateChanged",
                       "ttsStarted", "playbackDrained", "playbackFailed"):
            self.assertRegex(gate, rf"@Synchronized\s+fun {method}\(")
        # Duplicate readiness must not erase THINKING/SPEAKING or microphone readiness.
        self.assertIn("if (ready) return", body(gate, "fun connected()"))
        self.assertIn("playbackPending = false", body(gate, "fun connected()"))

    def test_dead_recorder_is_retryable_but_permission_is_terminal(self):
        recorder = source("audio/AudioRecorderManager.kt")
        self.assertIn("private val onRecoverableError: (String) -> Unit = onError", recorder)
        # Runtime error codes, blocked STOP and generation fencing are exercised by
        # test_android_audio_core.py against production Java, not source spelling.
        self.assertIn("CapturePump(source", recorder)
        self.assertIn('catch (_: SecurityException)', recorder)
        self.assertIn('onError("permission_loss")', recorder)
        self.assertIn("generation.matches(token)", recorder)
        self.assertIn("generation.activate(token)", recorder)
        self.assertIn("generation.invalidate(captureToken)", body(recorder, "fun stopAndJoin("))
        self.assertIn("pump?.stop(timeoutMs)", recorder)
        initialized = body(source("service/NexoKioskService.kt"), "fun initializeConnection(")
        self.assertRegex(initialized, r'publish\(NexoState.DISCONNECTED, "Menghubungkan ke server\.\.\."\)\s+if \(!foregroundAllowed\) return')
        self.assertIn("stopAndJoin()", body(recorder, "fun startRecording()"))
        options = source("audio/CaptureOptions.kt")
        self.assertIn("MediaRecorder.AudioSource.VOICE_COMMUNICATION", options)
        self.assertIn("CaptureOptions.legacyComparison()", recorder)
        self.assertIn("32, 3200", recorder)
        self.assertIn("if (options.requestAec)", recorder)
        self.assertIn("if (options.requestNs)", recorder)
        self.assertIn("sessionNs?.release()", recorder)
        echo_helper = source("audio/EchoCancellationHelper.kt")
        self.assertIn("fun attachNoiseSuppressor", echo_helper)
        self.assertIn("NoiseSuppressor.isAvailable()", echo_helper)

    def test_recorder_recovery_is_bounded_cancelled_and_session_fenced(self):
        service = source("service/NexoKioskService.kt")
        session = body(service, "private fun createAudioSession()")
        self.assertIn("onRecoverableError =", session)
        self.assertIn("recoverAudioSession(message)", session)
        recover = body(service, "private fun recoverAudioSession(")
        self.assertIn("failAudioSession(message)", recover)
        self.assertIn("audioRecoveryAttempts >= 3", recover)
        self.assertIn("checkSelfPermission", recover)
        self.assertIn("postDelayed", recover)
        self.assertIn("if (!foregroundAllowed) return", recover)
        self.assertIn("stopSelf()", body(service, "fun stopVoice()"))
        self.assertIn("catch (e: IllegalStateException)", body(service, "fun startVoice()"))
        self.assertIn("catch (e: SecurityException)", body(service, "fun startVoice()"))
        self.assertIn("token == generation", recover)
        self.assertIn("isVoiceEnabled", recover)
        self.assertIn("forceReconnect = true", recover)
        self.assertNotIn("putBoolean", recover)
        for marker in ("fun stopVoice()", "private fun failAudioSession(", "override fun onDestroy()"):
            self.assertIn("cancelAudioRecovery()", body(service, marker))
        connected = body(service, "override fun onConnected() {")
        self.assertIn("if (audioRecorder.isRecording()) return", connected)
        self.assertNotIn("else failAudioSession", connected)  # typed recorder callback owns failures
        self.assertIn("30_000L", connected)  # stable capture, not handshake, resets budget
        changed = body(service, "override fun onStateChanged(state: NexoState, message: String?, expression: String?) {")
        self.assertIn("audioRecovery != null", changed)  # queued idle cannot overwrite recovery ERROR

    def test_sticky_restart_restores_endpoint_only_for_saved_on(self):
        service = source("service/NexoKioskService.kt")
        started = body(service, "override fun onStartCommand(")
        self.assertIn("if (!isVoiceEnabled)", started)
        self.assertIn("return START_NOT_STICKY", started)
        self.assertIn("return START_STICKY", started)
        self.assertIn("checkSelfPermission", started)
        self.assertIn("foregroundAllowed", started)
        self.assertIn("restoreConnectionKey()", started)
        self.assertIn("initializeConnection(it.first, it.second)", started)
        self.assertNotIn("voiceEnabled = true", started)
        restored = body(service, "private fun restoreConnectionKey()")
        self.assertIn("ServerEndpointPolicy.isValid", restored)
        initialized = body(service, "fun initializeConnection(")
        self.assertIn('putString("server_url", serverUrl)', initialized)
        self.assertIn('putString("device_id", deviceId)', initialized)
        self.assertNotIn("bearerToken", restored)
        # Native task removal is not a user privacy stop or a resurrection hook.
        removed = body(service, "override fun onTaskRemoved(")
        self.assertIn("super.onTaskRemoved(rootIntent)", removed)
        for forbidden in ("stopVoice(", "stopSelf(", "startService(", "startForegroundService(", "AlarmManager"):
            self.assertNotIn(forbidden, removed)
        import xml.etree.ElementTree as ET
        manifest = ET.parse(ROOT / "android/app/src/main/AndroidManifest.xml")
        ns = "{http://schemas.android.com/apk/res/android}"
        kiosk = next(s for s in manifest.findall("application/service") if s.get(ns + "name") == ".service.NexoKioskService")
        self.assertEqual(kiosk.get(ns + "stopWithTask"), "false")

    def test_background_start_denial_is_actionable_not_retried(self):
        service = source("service/NexoKioskService.kt")
        notification = body(service, "private fun startForegroundNotification()")
        self.assertIn("catch (e: SecurityException)", notification)
        self.assertIn("catch (e: IllegalStateException)", notification)
        self.assertIn("foregroundAllowed = false", service)
        denied = body(service, "private fun foregroundDenied()")
        self.assertIn("stopSelf()", denied)
        self.assertNotIn("putBoolean", denied)
        self.assertNotIn("postDelayed", denied)
        activity = source("MainActivity.kt")
        bind = body(activity, "private fun bindKioskService()")
        self.assertIn("catch (e: SecurityException)", bind)
        self.assertIn("catch (e: IllegalStateException)", bind)
        self.assertIn("bindingRequested = false", bind)
        self.assertIn("PendingIntent.getActivity", notification)


if __name__ == "__main__":
    unittest.main()
