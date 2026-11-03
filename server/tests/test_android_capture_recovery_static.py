"""Source-only v2 fail-closed guards; not Android runtime/build/timing evidence.

The production Java ownership/capture/generation gates execute independently via
OwnershipHarness/CaptureHarness/GenerationHarness in test_android_audio_core.py.
These source checks do not execute the Kotlin service or establish hardware recovery.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
JAVA = ROOT / "android/app/src/main/java/id/my/yhdev/nexo"


def source(relative):
    return (JAVA / relative).read_text()


def body(text, marker):
    offset = text.index(marker)
    assert "{" in text[offset:].splitlines()[0], f"Expected block body: {marker}"
    start = text.index("{", offset)
    depth, end = 1, start + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start + 1:end - 1]


class CaptureRecoveryStaticTests(unittest.TestCase):
    def test_legacy_duplex_gate_reads_and_transitions_still_share_monitor(self):
        gate = source("audio/HalfDuplexGate.kt")
        self.assertRegex(gate, r"val canSendAudio: Boolean\s+@Synchronized get")
        for method in ("connected", "disconnected", "microphoneReady", "stateChanged",
                       "ttsStarted", "playbackDrained", "playbackFailed"):
            self.assertRegex(gate, rf"@Synchronized\s+fun {method}\(")
        self.assertIn("if (ready) return", body(gate, "fun connected()"))
        self.assertIn("playbackPending = false", body(gate, "fun connected()"))

    def test_recorder_error_types_and_generation_fences_are_preserved(self):
        recorder = source("audio/AudioRecorderManager.kt")
        self.assertIn("private val onRecoverableError: (String) -> Unit = onError", recorder)
        # Typed low-level errors do NOT grant the v2 service permission to retry.
        self.assertIn("CapturePump(source", recorder)
        self.assertIn('catch (_: SecurityException)', recorder)
        self.assertIn('onError("permission_loss")', recorder)
        self.assertIn('if (code == "permission_loss") onError(code) else onRecoverableError(code)', recorder)
        self.assertIn("generation.matches(token)", recorder)
        self.assertIn("generation.activate(token)", recorder)
        stop = body(recorder, "fun stopAndJoin(")
        self.assertLess(stop.index("generation.invalidate(captureToken)"), stop.index("pump?.stop(timeoutMs)"))
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

    def test_recorder_failure_cancels_and_requires_explicit_retry_not_reconnect(self):
        service = source("service/NexoKioskService.kt")
        for marker in ("private fun startCapture(", "private fun startResumedRecorder("):
            capture = body(service, marker)
            self.assertIn("onError =", capture)
            self.assertIn("onRecoverableError =", capture)
            self.assertEqual(capture.count("captureFailed(capture,"), 3)
        failed = body(service, "private fun captureFailed(")
        self.assertIn("captureAdmission.revoke(capture)", failed)
        self.assertIn("if (revoked < 0) return", failed)
        self.assertNotIn("local.fail(", failed)
        self.assertNotIn("lifecycle.execute", failed)
        self.assertIn("destroyed || revoked != captureGeneration", failed)
        self.assertIn('fail("Rekaman gagal: $code")', failed)
        fail = body(service, "private fun fail(")
        self.assertIn('closeRecordingSheet("error")', fail)
        self.assertIn('pauseLocal("error")', fail)
        self.assertIn("Tutup dan buka lagi", fail)
        pause = body(service, "private fun pauseLocal(")
        for fragment in ("persistOff()", "ownership.disconnected()", "fenceOutput()",
                         "invalidateCapture(reason)", "webSocketClient?.disconnect()"):
            self.assertIn(fragment, pause)
        for forbidden in ("postDelayed", "ensureConnection()", "requestResume()", "startRecording()"):
            self.assertNotIn(forbidden, failed + fail + pause)
        self.assertNotIn("recoverAudioSession", service)
        self.assertNotIn("audioRecoveryAttempts", service)
        self.assertIn("override fun onDisconnected() = dispatch { fail(", service)
        self.assertIn("stopSelf()", body(service, "fun stopVoice()"))

    def test_process_restart_is_non_sticky_paused_and_cannot_restore_saved_on(self):
        service = source("service/NexoKioskService.kt")
        self.assertRegex(service, r"override fun onStartCommand\([^\n]+\): Int = START_NOT_STICKY")
        self.assertNotRegex(service, r"\bSTART_STICKY\b")
        self.assertIn("persistOff()", body(service, "override fun onCreate()"))
        self.assertNotIn('getBoolean("enabled"', service)
        self.assertNotIn("restoreConnectionKey()", service)
        initialized = body(service, "fun initializeConnection(")
        self.assertIn("ServerEndpointPolicy.isValid", initialized)
        self.assertIn('putString("server_url", serverUrl)', initialized)
        self.assertIn('putString("device_id", deviceId)', initialized)
        self.assertNotIn("credentialStore.read", initialized)
        self.assertNotIn("connect()", initialized)
        removed = body(service, "override fun onTaskRemoved(")
        self.assertIn("setUiForeground(false)", removed)
        self.assertIn("super.onTaskRemoved(rootIntent)", removed)
        foreground = body(service, "fun setUiForeground(")
        self.assertIn('closeRecordingSheet("background")', foreground)
        self.assertIn('pauseLocal("background")', foreground)
        for forbidden in ("startService(", "startForegroundService(", "AlarmManager", "requestResume()"):
            self.assertNotIn(forbidden, removed + foreground)
        import xml.etree.ElementTree as ET
        manifest = ET.parse(ROOT / "android/app/src/main/AndroidManifest.xml")
        ns = "{http://schemas.android.com/apk/res/android}"
        kiosk = next(s for s in manifest.findall("application/service") if s.get(ns + "name") == ".service.NexoKioskService")
        self.assertEqual(kiosk.get(ns + "stopWithTask"), "false")

    def test_background_start_denial_is_actionable_paused_and_not_retried(self):
        service = source("service/NexoKioskService.kt")
        notification = body(service, "private fun startForegroundNotification()")
        for exception in ("SecurityException", "IllegalStateException"):
            self.assertRegex(notification, rf"catch \([^:]+: {exception}\)")
            denied = body(notification, f": {exception})")
            self.assertIn("foregroundAllowed = false", denied)
            self.assertIn("persistOff()", denied)
            self.assertIn("publish(NexoState.ERROR", denied)
            self.assertNotIn("postDelayed", denied)
            self.assertNotIn("startRecording()", denied)
        self.assertIn("Izin layanan mikrofon diperlukan", notification)
        self.assertIn("Buka aplikasi untuk memakai mikrofon", notification)
        self.assertIn("PendingIntent.getActivity", notification)
        permitted = service.split("private fun permitted() =", 1)[1].split("private fun persistOff()", 1)[0]
        for gate in ("uiForeground", "foregroundAllowed", "RECORD_AUDIO", "PERMISSION_GRANTED"):
            self.assertIn(gate, permitted)
        bind = body(source("MainActivity.kt"), "private fun bindKioskService()")
        self.assertIn("catch (e: SecurityException)", bind)
        self.assertIn("catch (e: IllegalStateException)", bind)
        self.assertIn("bindingRequested = false", bind)

    def test_service_recorder_joins_are_confined_to_lifecycle_worker_blocks(self):
        service = source("service/NexoKioskService.kt")
        self.assertIn('Executors.newSingleThreadExecutor { Thread(it, "Nexo-AudioLifecycle") }', service)
        workers = [body(service[match.start():], "lifecycle.execute")
                   for match in re.finditer(r"lifecycle\.execute\s*\{", service)]
        for join in ("stopAndJoin()", "finishAndJoin()"):
            self.assertGreater(service.count(join), 0)
            self.assertEqual(service.count(join), sum(worker.count(join) for worker in workers))
        self.assertNotIn(".stopRecording()", service)
        invalidation = body(service, "private fun invalidateCapture(")
        ordered = ["webSocketClient?.fenceCapture()", "nextCapture()",
                   "webSocketClient?.cancelUtterance()", "sealedPending = null", "lifecycle.execute", "local.cancel(reason)"]
        self.assertIn("val recorder = audioRecorder", invalidation)
        self.assertNotIn("audioRecorder?.stopAndJoin()", invalidation)
        positions = [invalidation.index(fragment) for fragment in ordered]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("onEnrollmentLevel(0f)", invalidation)
        self.assertIn("onTelemetryReceived(0f, null)", invalidation)


if __name__ == "__main__":
    unittest.main()
