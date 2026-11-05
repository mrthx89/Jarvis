package id.my.yhdev.nexo.service

import android.Manifest
import android.app.*
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.BitmapFactory
import android.graphics.drawable.Icon
import android.media.AudioManager
import android.media.MediaMetadata
import android.media.session.MediaSession
import android.media.session.PlaybackState
import android.os.*
import android.widget.RemoteViews
import androidx.core.app.NotificationCompat
import com.google.gson.JsonObject
import id.my.yhdev.nexo.MainActivity
import id.my.yhdev.nexo.R
import id.my.yhdev.nexo.audio.*
import id.my.yhdev.nexo.audio.core.AudioOwnership
import id.my.yhdev.nexo.audio.core.LocalCapture
import id.my.yhdev.nexo.audio.core.ProcessingDeadline
import id.my.yhdev.nexo.audio.core.UtteranceBuffer
import id.my.yhdev.nexo.audio.vad.SileroVad
import id.my.yhdev.nexo.config.ServerEndpointPolicy
import id.my.yhdev.nexo.network.*
import id.my.yhdev.nexo.security.DeviceCredentialStore
import id.my.yhdev.nexo.ui.NexoState
import java.util.UUID
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/** All binder/UI state runs on main. Recorder joins and model disposal run only on lifecycle. */
open class NexoKioskService : Service() {
    private val binder = LocalBinder()
    private val main = Handler(Looper.getMainLooper())
    private val lifecycle = Executors.newSingleThreadExecutor { Thread(it, "Nexo-AudioLifecycle") }
    private val upload = Executors.newSingleThreadExecutor { Thread(it, "Nexo-AudioUpload") }
    private val ownership = AudioOwnership()
    private val local = LocalCapture()
    private val playbackTurn = AtomicInteger(0)
    @Volatile private var generation = 0
    private val captureAdmission = id.my.yhdev.nexo.audio.core.LifecycleAdmission()
    private val captureGeneration: Long get() = captureAdmission.generation()
    private fun nextCapture(): Long = captureAdmission.advance()
    @Volatile private var webSocketClient: NexoWebSocketClient? = null
    @Volatile private var audioRecorder: AudioRecorderManager? = null
    private lateinit var audioPlayer: AudioPlayerManager
    private lateinit var earconManager: EarconManager
    private lateinit var diagnostic: PrivateAudioDiagnostic
    private val credentialStore by lazy { DeviceCredentialStore(this) }
    private val voicePreferences by lazy { getSharedPreferences("voice_control", Context.MODE_PRIVATE) }
    @Volatile private var voiceEnabled = false
    private var uiForeground = false
    private var foregroundAllowed = true
    private var connectionKey: Pair<String, String>? = null
    private var currentState = NexoState.IDLE
    private var currentMessage: String? = "Paused • tekan Mulai untuk melanjutkan"
    private var currentExpression: String? = null
    private var sleepExpression: String? = null
    private var sheetPurpose = "enrollment"
    private var sheetState = "CLOSED"
    private var sheetMessage = "Percakapan dijeda"
    private var sheetOpened = 0L
    private var sheetLocalStopped = false
    private var acquireSent = false
    private var querySent = false
    private var queryRequest: String? = null
    private var queryDeadline = 0L
    private var pendingDelete: String? = null
    private var deleteDeadline = 0L
    private var releaseLocallyStopped = false
    private var releaseAcknowledged = false
    private var releaseDeadline = 0L
    private var resumeOnRelease = false
    private fun completeReleaseIfReady() {
        if (!releaseLocallyStopped || !releaseAcknowledged) return
        sheetOwner = null
        sheet("CLOSED", "Paused • rekaman ditutup")
        if (resumeOnRelease && permitted()) { resumeOnRelease = false; requestResume() }
    }
    private var resumeOnHello = false
    private var retryResume = false
    private fun transientDisconnect() {
        resumeOnHello = false; resumeOnRelease = false; retryResume = false
        persistOff()
        playbackReady = false; playbackPending = false; turnComplete = false
        ownership.disconnected()
        webSocketClient?.fenceOutput()
        earconManager.stop(); audioPlayer.stop()
        if (sheetOwner != null) { fail("Koneksi panel terputus • rekaman khusus dibatalkan"); return }
        val preserve = !startingCapture && recovery.disconnected(activePurpose, local.state(), pendingUtterance != null || sealedPending != null)
        if (!preserve) invalidateCapture("network_idle")
        publish(NexoState.DISCONNECTED, if (preserve && audioRecorder?.isRecording() == true)
            "Merekam lokal • koneksi terputus, tekan Selesai (maksimal 20 detik). Tidak ada jawaban offline."
            else "Paused • memeriksa status ucapan saat koneksi pulih, mikrofon tetap OFF", sleepExpression)
        scheduleAutoRestart()
    }
    private var enrollmentId: String? = null
    private var enrollmentRequest: String? = null
    private var enrollmentOperation: String? = null
    private var enrollmentDeadline = 0L
    private val samples = mutableListOf<String>()
    private var sampleCount = 0
    private var pendingUtterance: String? = null
    private val processing = ProcessingDeadline()
    private val recovery = id.my.yhdev.nexo.audio.core.ConversationRecovery()
    private var sealedPending: UtteranceBuffer.Sealed? = null
    private var finishPending = false
    private var startingCapture = false
    private var turnComplete = false
    @Volatile private var playbackReady = false
    private var playbackPending = false
    private var playbackDrainedAt = 0L
    private var diagnosticUntil = 0L
    private var diagnosticReady = false
    private val diagnosticOperation = id.my.yhdev.nexo.audio.core.SheetOperation()
    private var automatic = true // Default to local Silero VAD; falls back to manual only if VAD unavailable.
    private var modeConfiguredByUser = false
    @Volatile private var vadAvailable = false
    private var vad: SileroVad? = null // Lifecycle owns reset/close after consumer join.
    private var lastVoiceprintDialog: VoiceprintDialog? = null
    @Volatile private var destroyed = false
    private var keepVoiceAlive = false
    private var autoRestartAt = 0L
    private fun scheduleAutoRestart(message: String? = null) {
        val fatal = !permitted() || sheetOwner != null || (message != null && (
            message.contains("Penyiapan perangkat") || message.contains("Endpoint") ||
            message.contains("Izin") || message.contains("pembaruan audio v2") ||
            message.contains("Buka aplikasi")))
        if (fatal) {
            keepVoiceAlive = false
            autoRestartAt = 0L
            persistOff()
        } else if (keepVoiceAlive) {
            autoRestartAt = now() + 1200L
            voiceEnabled = true
            serviceCallback?.onVoiceEnabledChanged(true)
        }
    }
    val isVoiceEnabled: Boolean get() = voiceEnabled
    val canFinishCapture: Boolean get() = !startingCapture && !finishPending &&
        audioRecorder?.isRecording() == true && local.state() in setOf("CAPTURING", "SEALED")
    val hasRecoveryCapture: Boolean get() = recovery.active() && canFinishCapture
    val canResumeVoice: Boolean get() = !recovery.active() || webSocketClient?.uploader?.state() == "RETRY_READY"

    data class RecordingSession(val state: String, val message: String, val ready: Boolean,
        val sampleCount: Int, val requiredSamples: Int, val diagnostic: Boolean)
    private fun session() = RecordingSession(sheetState, sheetMessage,
        ownership.dedicatedAllowed(now()) && sheetState in setOf("READY", "SAMPLES_READY", "LOCAL_READY"),
        samples.size, sampleCount, sheetPurpose == "diagnostic")
    var serviceCallback: ServiceCallback? = null
        set(value) {
            field = value
            value?.onVoiceEnabledChanged(voiceEnabled)
            value?.onStateChanged(currentState, currentMessage, currentExpression)
            value?.onRecordingSessionChanged(session())
            value?.onCaptureModeChanged(automatic, vadAvailable)
            lastVoiceprintDialog?.let { value?.onVoiceprintDialogReceived(it) }
        }
    interface ServiceCallback {
        fun onVoiceEnabledChanged(enabled: Boolean)
        fun onStateChanged(state: NexoState, message: String?, expression: String?)
        fun onTelemetryReceived(amplitude: Float, bands: List<Float>?)
        fun onEnrollmentLevel(amplitude: Float) {}
        fun onTranscriptionReceived(role: String, text: String, isFinal: Boolean)
        fun onVoiceprintDialogReceived(dialog: VoiceprintDialog)
        fun onRecordingSessionChanged(session: RecordingSession) {}
        fun onCaptureModeChanged(automatic: Boolean, available: Boolean) {}
    }
    inner class LocalBinder : Binder() { fun getService(): NexoKioskService = this@NexoKioskService }
    override fun onBind(intent: Intent?): IBinder = binder
    private fun now() = SystemClock.elapsedRealtime()
    private fun uuid() = UUID.randomUUID().toString()
    private fun permitted() = (uiForeground || keepVoiceAlive) && foregroundAllowed &&
        checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
    private fun persistOff() {
        voiceEnabled = keepVoiceAlive && permitted() && sheetOwner == null
        if (!voiceEnabled) {
            voiceEnabled = false
            voicePreferences.edit().putBoolean("enabled", false).commit()
            serviceCallback?.onVoiceEnabledChanged(false)
        }
        updateOngoingNotification(currentState, currentMessage, currentExpression)
    }
    private var captureNotice = ""
    private fun withCaptureNotice(message: String): String =
        if (captureNotice.isEmpty() || message.contains("duration_limit")) message else message + captureNotice
    private fun publish(state: NexoState, message: String?, expression: String? = null) {
        currentState = state; currentMessage = message?.let(::withCaptureNotice); currentExpression = expression
        serviceCallback?.onStateChanged(state, currentMessage, expression)
        updateOngoingNotification(state, currentMessage, expression)
    }
    private fun sheet(state: String, message: String) {
        sheetState = state; sheetMessage = withCaptureNotice(message)
        serviceCallback?.onRecordingSessionChanged(session())
    }
    private fun outputGuard(connection: String, epoch: Long, action: () -> Unit): Boolean {
        if (destroyed || !ownership.outputAllowed(connection, epoch)) return false
        return ownership.runOutput(connection, epoch) { action() }
    }
    private fun outputGuard(action: () -> Unit): Boolean {
        val id = ownership.connection() ?: return false
        return outputGuard(id, ownership.epoch(), action)
    }

    override fun onCreate() {
        super.onCreate()
        // A process/service restart never inherits permission to resume an uncertain turn.
        persistOff()
        audioPlayer = newPlayer()
        earconManager = EarconManager(this, ::outputGuard)
        diagnostic = PrivateAudioDiagnostic(this)
        startForegroundNotification()
        main.post(tick)
        lifecycle.execute {
            vad = runCatching {
                val file = noBackupFilesDir.resolve("silero-v6.2.3.onnx")
                assets.open("vad/silero-v6.2.3.onnx").use { source -> file.outputStream().use { source.copyTo(it) } }
                SileroVad(file.absolutePath)
            }.getOrNull()
            vadAvailable = vad != null
            main.post {
                if (!destroyed) {
                    if (!modeConfiguredByUser) automatic = vadAvailable
                    serviceCallback?.onCaptureModeChanged(automatic, vadAvailable)
                }
            }
        }
    }
    private fun newPlayer(): AudioPlayerManager {
        val connection = ownership.connection()
        val epoch = ownership.epoch()
        return AudioPlayerManager(onError = { main.post {
            if (connection != null && ownership.outputAllowed(connection, epoch)) fail("Pemutaran audio gagal")
        } }, outputGuard = { action -> playbackReady && connection != null && outputGuard(connection, epoch, action) })

    }
    companion object {
        const val NOTIFICATION_ID = 1001
        const val NOTIFICATION_CHANNEL_ID = "nexo_kiosk_service"
        const val ACTION_STOP_VOICE = "id.my.yhdev.nexo.action.STOP_VOICE"
    }
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_NOT_STICKY.also {
        if (intent?.action == ACTION_STOP_VOICE) stopVoice()
    }
    override fun onTaskRemoved(rootIntent: Intent?) {
        setUiForeground(false)
        super.onTaskRemoved(rootIntent)
    }
    fun setUiForeground(value: Boolean) {
        uiForeground = value
        if (!value) {
            closeRecordingSheet("background")
            if (!keepVoiceAlive) {
                pauseLocal("background")
            }
        }
        ownership.foreground(((value || keepVoiceAlive) && foregroundAllowed) && permitted())
        updateOngoingNotification(currentState, currentMessage, currentExpression)
    }
    fun setAutomaticCapture(value: Boolean): Boolean {
        val busy = startingCapture || audioRecorder?.isRecording() == true || pendingUtterance != null ||
            sealedPending != null || finishPending || playbackPending || now() - playbackDrainedAt < 600
        if (value && !vadAvailable || sheetOwner != null || !ownership.pauseForModeChange(busy)) return false
        modeConfiguredByUser = true
        automatic = value
        keepVoiceAlive = false; autoRestartAt = 0L
        pauseLocal("mode_changed")
        publish(NexoState.IDLE, "Paused • mode diubah, tekan Mulai untuk melanjutkan", sleepExpression)
        serviceCallback?.onCaptureModeChanged(automatic, vadAvailable)
        return true
    }
    fun startVoice() {
        if (!permitted() || ownership.owner() != null || sheetOwner != null) return
        keepVoiceAlive = true; autoRestartAt = 0L
        if (ownership.normalAllowed()) return
        if (recovery.active()) {
            if (webSocketClient?.uploader?.state() != "RETRY_READY") return
            retryResume = true
            requestResume()
            return
        }
        resumeOnHello = true
        ensureConnection()
        if (webSocketClient?.outdoorSession != null) requestResume()
        updateOngoingNotification(currentState, currentMessage, currentExpression)
    }
    private fun requestResume() {
        resumeOnHello = false
        if (!permitted()) return
        val request = uuid()
        if (!ownership.resume(request, now())) return
        if (retryResume) persistOff()
        else {
            keepVoiceAlive = true; autoRestartAt = 0L
            voicePreferences.edit().putBoolean("enabled", true).commit()
            voiceEnabled = true
            serviceCallback?.onVoiceEnabledChanged(true)
        }
        publish(NexoState.IDLE, "Menunggu izin lanjut dari server • mikrofon belum aktif", sleepExpression)
        if (webSocketClient?.sendOutdoorControl("conversation_resume", request) != true) fail("Permintaan lanjut gagal")
    }
    fun interruptSpeaking(): Boolean {
        if (!isVoiceEnabled || !playbackPending) return false
        playbackPending = false
        earconManager.stop()
        audioPlayer.stop()
        webSocketClient?.sendOutdoorControl("user_interrupted", uuid())
        turnComplete = true
        playbackDrainedAt = now()
        maybeNextTurn()
        return true
    }
    fun stopVoice() {
        keepVoiceAlive = false; autoRestartAt = 0L
        resumeOnHello = false; resumeOnRelease = false
        closeRecordingSheet("STOP")
        pauseLocal("STOP")
        updateOngoingNotification(currentState, currentMessage, currentExpression)
        stopSelf()
    }
    /** Closes the actual output/send gates before any asynchronous cleanup. */
    private fun pauseLocal(reason: String) {
        webSocketClient?.fenceCapture()
        sheetOwner = null; enrollmentOperation = null; queryRequest = null; pendingDelete = null
        playbackReady = false; playbackPending = false; resumeOnRelease = false
        persistOff()
        ownership.disconnected()
        webSocketClient?.fenceOutput()
        invalidateCapture(reason)
        earconManager.stop()
        audioPlayer.stop()
        ++generation
        webSocketClient?.disconnect(); webSocketClient = null
        lastVoiceprintDialog = null
        publish(NexoState.IDLE, "Paused • tekan Mulai untuk melanjutkan", sleepExpression)
    }
    private fun fail(message: String) {
        if (destroyed) return
        resumeOnHello = false; resumeOnRelease = false
        closeRecordingSheet("error")
        pauseLocal("error")
        sheet("ERROR", "$message. Tutup dan buka lagi untuk mencoba ulang.")
        publish(NexoState.ERROR, message, sleepExpression)
        scheduleAutoRestart(message)
    }
    fun initializeConnection(serverUrl: String, deviceId: String, forceReconnect: Boolean = false) {
        if (!ServerEndpointPolicy.isValid(serverUrl) || deviceId.isBlank()) { fail("Endpoint suara tidak valid"); return }
        val changed = connectionKey != null && connectionKey != Pair(serverUrl, deviceId)
        if (changed || forceReconnect) { keepVoiceAlive = false; autoRestartAt = 0L; closeRecordingSheet("endpoint_changed"); pauseLocal("endpoint_changed") }
        connectionKey = Pair(serverUrl, deviceId)
        voicePreferences.edit().putString("server_url", serverUrl).putString("device_id", deviceId).apply()
    }
    fun refreshCredentials() { keepVoiceAlive = false; autoRestartAt = 0L; closeRecordingSheet("credentials_changed"); pauseLocal("credentials_changed") }
    private fun ensureConnection() {
        if (webSocketClient != null) return
        val key = connectionKey ?: run { fail("Endpoint belum tersedia"); return }
        val bearer = credentialStore.read(key.first, key.second) ?: run { fail("Penyiapan perangkat diperlukan"); return }
        val token = ++generation
        val events = object : NexoWebSocketClient.WebSocketEventListener {
            private fun dispatch(action: () -> Unit) { main.post { if (!destroyed && token == generation) action() } }
            override fun usesTaggedOutput() = true
            override fun onOutdoorProtocolReady(session: OutdoorSession) = dispatch {
                // No connected/reconnect callback enables the mic. Only a pending explicit action may proceed.
                ownership.connected(session.connectionId, session.outputEpoch)
                ownership.foreground(permitted())
                recovery.remember(session.serverInstanceId)
                if (recovery.active()) {
                    if (recovery.instance() != session.serverInstanceId) {
                        fail("delivery_unknown • server berubah, ucapan tidak diputar ulang"); return@dispatch
                    }
                    publish(NexoState.IDLE, "Paused • koneksi pulih, memeriksa status ucapan; mikrofon tetap OFF", sleepExpression)
                }
                if (sheetState == "ACQUIRING") {
                    ownership.openSheet(sheetOwner ?: return@dispatch, sheetOpened)
                    tryAcquire()
                } else if (resumeOnHello) requestResume()
            }
            override fun onConnected() {}
            override fun onDisconnected() = dispatch { transientDisconnect() }
            override fun onOutdoorControl(message: JsonObject) = dispatch { control(message) }
            private var outputBarrier = java.util.concurrent.CompletableFuture<Boolean>()
            override fun onOutputControl(message: JsonObject) {
                if (destroyed || token != generation) return
                val connection = OutdoorProtocol.uuid(message, "connection_id") ?: return
                val outputEpoch = OutdoorProtocol.integer(message, "output_epoch", 0, Long.MAX_VALUE) ?: return
                if (!ownership.outputAllowed(connection, outputEpoch)) return
                val start = OutdoorProtocol.string(message, "type") == "tts_start"
                val barrier = if (start) java.util.concurrent.CompletableFuture<Boolean>().also { outputBarrier = it } else outputBarrier
                main.post {
                    val id = OutdoorProtocol.uuid(message, "connection_id")
                    val epoch = OutdoorProtocol.integer(message, "output_epoch", 0, Long.MAX_VALUE)
                    if (destroyed || token != generation || id == null || epoch == null || !ownership.outputAllowed(id, epoch)) {
                        if (start) barrier.complete(false)
                        return@post
                    }
                    if (start) {
                        playbackReady = false
                        val capture = nextCapture()
                        val recorder = audioRecorder
                        lifecycle.execute {
                            local.cancel("server_output")
                            val stopped = recorder?.stopAndJoin() != false
                            main.post {
                                if (destroyed || token != generation || capture != captureGeneration || !ownership.outputAllowed(id, epoch)) {
                                    barrier.complete(false); return@post
                                }
                                // A timed-out barrier cannot later reopen this response.
                                if (barrier.isDone) return@post
                                playbackReady = stopped
                                barrier.complete(stopped)
                                if (!stopped) fail("Mikrofon belum berhenti sebelum jawaban")
                            }
                        }
                    }
                    output(message)
                }
            }
            override fun onOutdoorAudio(connectionId: String, epoch: Long, audioData: ByteArray) {
                if (destroyed || token != generation || !ownership.outputAllowed(connectionId, epoch)) return
                processing.activity(now())
                val barrier = outputBarrier
                val ready = runCatching { barrier.get(1600, TimeUnit.MILLISECONDS) }.getOrDefault(false)
                if (!ready) {
                    barrier.complete(false)
                    dispatch {
                        if (ownership.outputAllowed(connectionId, epoch)) fail("playback_barrier_timeout • jawaban dibatalkan")
                    }
                    return
                }
                if (token != generation || !ownership.outputAllowed(connectionId, epoch)) return
                val player = audioPlayer
                if (!player.writeChunk(audioData) && token == generation && ownership.outputAllowed(connectionId, epoch)) {
                    dispatch { fail("Pemutaran audio gagal") }
                }
            }
            override fun onStateChanged(state: NexoState, message: String?, expression: String?) = dispatch {
                if (state == NexoState.ERROR) fail(message ?: "Koneksi suara gagal")
                else if (state == NexoState.DISCONNECTED && !recovery.active())
                    publish(state, message, expression)
            }
            override fun onVoiceprintDialogReceived(dialog: VoiceprintDialog) = dispatch { voiceprint(dialog) }
            // Untagged callbacks cannot affect v2 state, output or microphone ownership.
            override fun onTelemetryReceived(amplitude: Float, bands: List<Float>?) {}
            override fun onTranscriptionReceived(role: String, text: String, isFinal: Boolean) {}
            override fun onAudioChunkReceived(audioData: ByteArray) {}
            override fun onTTSStarted() {}
            override fun onTTSEnded() {}
        }
        webSocketClient = NexoWebSocketClient(key.first, key.second, bearer, events).also { it.connect() }
    }

    private var sheetOwner: String? = null
    /** Called synchronously BEFORE constructing/showing the non-ready sheet shell. */
    fun openRecordingSheet(diagnosticOnly: Boolean = false): Boolean {
        if (!permitted() || sheetOwner != null || ownership.releasing()) return false
        sheetOwner = uuid(); sheetPurpose = if (diagnosticOnly) "diagnostic" else "enrollment"
        sheetOpened = now(); sheetLocalStopped = false; acquireSent = false; querySent = false
        queryRequest = null; pendingDelete = null; lastVoiceprintDialog = null
        enrollmentId = null; enrollmentRequest = null; enrollmentOperation = null
        samples.clear(); sampleCount = 0; resumeOnHello = false; resumeOnRelease = false
        keepVoiceAlive = false; autoRestartAt = 0L
        webSocketClient?.fenceCapture()
        persistOff()
        ownership.openSheet(sheetOwner!!, sheetOpened)
        webSocketClient?.fenceOutput()
        invalidateCapture("sheet_open")
        playbackPending = false
        earconManager.stop()
        sheet("ACQUIRING", "Menjeda percakapan dan menghentikan audio…")
        publish(NexoState.IDLE, "Paused • panel rekam memiliki mikrofon", sleepExpression)
        val playerStopped = java.util.concurrent.CompletableFuture<Boolean>()
        audioPlayer.stopAndConfirm { playerStopped.complete(it) }
        val earconsStopped = earconManager.stopAndConfirm()
        val owner = sheetOwner
        val recorder = audioRecorder
        lifecycle.execute {
            val stopped = recorder?.stopAndJoin() != false
            val outputStopped = runCatching { playerStopped.get(1500, TimeUnit.MILLISECONDS) }.getOrDefault(false)
            val chimesStopped = runCatching { earconsStopped.get(1500, TimeUnit.MILLISECONDS) }.getOrDefault(false)
            val diagnosticStopped = runCatching { diagnostic.dismiss().get(2, TimeUnit.SECONDS); true }.getOrDefault(false)
            main.post {
                if (owner != sheetOwner || sheetState != "ACQUIRING") return@post
                if (!stopped || !outputStopped || !chimesStopped || !diagnosticStopped) { fail("Audio lokal belum berhenti"); return@post }
                sheetLocalStopped = true
                ownership.localStopped(ownership.generation(), now())
            }
        }
        ensureConnection()
        tryAcquire()
        return sheetOwner != null
    }
    private fun tryAcquire() {
        if (sheetState != "ACQUIRING" || acquireSent || webSocketClient?.outdoorSession == null) return
        val request = uuid()
        if (!ownership.acquire(request, now())) { fail("Batas waktu kepemilikan mikrofon"); return }
        acquireSent = true
        // Stop may finish before socket negotiation/acquire: reapply its confirmed barrier now.
        if (sheetLocalStopped) ownership.localStopped(ownership.generation(), now())
        if (webSocketClient?.sendOutdoorControl("audio_owner_acquire", request,
                mapOf("owner_id" to sheetOwner!!, "purpose" to sheetPurpose, "policy" to "cancel_active")) != true)
            fail("Permintaan kepemilikan mikrofon gagal")
    }
    fun closeRecordingSheet(reason: String = "dismiss", resume: Boolean = false) {
        val owner = sheetOwner ?: return
        if (sheetState == "RELEASING") { if (resume && permitted()) resumeOnRelease = true; return }
        releaseLocallyStopped = false; releaseAcknowledged = false; releaseDeadline = now() + 2000
        resumeOnRelease = resume && permitted()
        persistOff()
        val request = uuid()
        val lease = ownership.lease()
        ownership.closeSheet(request, now()); webSocketClient?.fenceOutput()
        invalidateCapture(reason)
        diagnosticReady = false; diagnosticUntil = 0
        val closedCapture = captureGeneration
        val diagnosticStopped = diagnostic.dismiss()
        lifecycle.execute {
            val stopped = audioRecorder?.stopAndJoin() != false &&
                runCatching { diagnosticStopped.get(1500, TimeUnit.MILLISECONDS); true }.getOrDefault(false)
            main.post {
                if (closedCapture == captureGeneration) {
                    releaseLocallyStopped = stopped
                    if (!stopped) fail("Penghentian rekaman belum terkonfirmasi")
                    else completeReleaseIfReady()
                }
            }
        }
        enrollmentId?.let { id ->
            if (lease != null) webSocketClient?.sendOutdoorControl("enrollment_cancel", uuid(),
                mapOf("owner_id" to owner, "lease_id" to lease, "enrollment_id" to id))
        }
        enrollmentId = null; enrollmentOperation = null; pendingDelete = null; queryRequest = null
        samples.clear(); sampleCount = 0
        sheet("RELEASING", "Menutup rekaman • percakapan tetap Paused")
        val fields = mutableMapOf<String, Any>("owner_id" to owner, "reason" to reason)
        if (lease != null) fields["lease_id"] = lease
        if (webSocketClient?.sendOutdoorControl("audio_owner_release", request, fields) != true) {
            sheetOwner = null; pauseLocal("release_unconfirmed")
            sheet("CLOSED", "Paused • koneksi ditutup karena pelepasan belum terkonfirmasi")
        }
    }
    fun cancelVoiceprintEnrollment() { closeRecordingSheet("cancel") }
    fun resumeAfterRecording() { closeRecordingSheet("explicit_resume", resume = true) }
    fun recordingSession(): RecordingSession = session()
    fun queryVoiceprint() {
        if (!ownership.dedicatedAllowed(now()) || sheetPurpose != "enrollment" || querySent) return
        querySent = true; queryRequest = uuid(); queryDeadline = now() + 10000
        webSocketClient?.sendJson(VoiceprintControl("voiceprint_query", queryRequest!!))
    }
    fun unenrollVoiceprint() {
        if (!ownership.dedicatedAllowed(now()) || pendingDelete != null || enrollmentId != null ||
            sheetState != "READY" || lastVoiceprintDialog?.isEnrolled != true) return
        pendingDelete = uuid(); deleteDeadline = now() + 10000
        sheet("DELETING", "Menghapus profil setelah konfirmasi…")
        if (webSocketClient?.unenrollVoiceprint(pendingDelete!!) != true) fail("Penghapusan gagal dikirim")
    }
    private fun voiceprint(dialog: VoiceprintDialog) {
        if (sheetOwner == null || !ownership.dedicatedAllowed(now())) return
        if (dialog.requestId != queryRequest && dialog.requestId != pendingDelete) return
        if (dialog.requestId == null) return
        if (dialog.requestId == pendingDelete) {
            pendingDelete = null
            sheet("READY", dialog.message ?: "Penghapusan selesai • percakapan tetap Paused")
        } else queryRequest = null
        lastVoiceprintDialog = dialog
        serviceCallback?.onVoiceprintDialogReceived(dialog)
    }
    fun startVoiceprintEnrollment(): Boolean {
        if (!permitted() || !ownership.dedicatedAllowed(now()) || sheetPurpose != "enrollment" ||
            lastVoiceprintDialog?.available != true || sheetState !in setOf("READY", "SAMPLES_READY")) return false
        if (enrollmentId == null) {
            enrollmentId = uuid(); enrollmentRequest = uuid(); enrollmentOperation = "begin"
            enrollmentDeadline = now() + 10000
            sheet("STARTING", "Meminta sesi sampel dan aturan kualitas server…")
            return sendEnrollment("enrollment_begin", enrollmentRequest!!)
        }
        if (samples.size >= sampleCount + 1 || sampleCount == 0) return false
        return startCapture("enrollment", false)
    }
    private fun sendEnrollment(type: String, request: String, extra: Map<String, Any> = emptyMap()): Boolean {
        val fields = mutableMapOf<String, Any>("owner_id" to (sheetOwner ?: return false),
            "lease_id" to (ownership.lease() ?: return false), "enrollment_id" to (enrollmentId ?: return false))
        fields.putAll(extra)
        val sent = webSocketClient?.sendOutdoorControl(type, request, fields) == true
        if (!sent) fail("Kontrol pendaftaran gagal dikirim")
        return sent
    }
    fun saveVoiceprintEnrollment(): Boolean {
        if (!ownership.dedicatedAllowed(now()) || sampleCount < 2 || samples.size != sampleCount + 1 ||
            pendingUtterance != null || sheetState != "SAMPLES_READY") return false
        enrollmentOperation = "save"; enrollmentRequest = uuid(); enrollmentDeadline = now() + 30000
        sheet("SAVING", "Memeriksa sampel dan holdout di server sebelum menyimpan…")
        return sendEnrollment("enrollment_save", enrollmentRequest!!,
            mapOf("sample_utterance_ids" to samples.take(sampleCount), "holdout_utterance_id" to samples.last()))
    }
    fun startDiagnosticCapture(): Boolean {
        if (!ownership.dedicatedAllowed(now()) || sheetPurpose != "diagnostic" || sheetState != "READY") return false
        diagnosticReady = false
        return startCapture("diagnostic", false)
    }
    fun playDiagnostic(): Boolean {
        if (!ownership.dedicatedAllowed(now()) || sheetState != "LOCAL_READY" || !diagnosticReady || now() >= diagnosticUntil) return false
        sheet("LOCAL_PLAYBACK", "Memutar klip privat • percakapan tetap Paused")
        val ticket = diagnosticOperation.begin(sheetOwner, sheetState, captureGeneration)
        val operation = diagnostic.play(true)
        lifecycle.execute {
            val played = runCatching { operation.get(2, TimeUnit.SECONDS) }.getOrDefault(false)
            if (!played) diagnostic.fencePlayback()
            main.post {
                if (!destroyed && diagnosticOperation.accepts(ticket, sheetOwner, sheetState, captureGeneration))
                    sheet("LOCAL_READY", if (played) "Klip diputar lokal. Tutup untuk menghapus." else "Klip tidak tersedia. Rekam ulang.")
            }
        }
        return true
    }
    fun deleteDiagnostic() {
        if (sheetPurpose != "diagnostic" || sheetState !in setOf("LOCAL_READY", "LOCAL_PLAYBACK")) return
        diagnosticReady = false; diagnosticUntil = 0
        diagnostic.fencePlayback()
        sheet("LOCAL_DELETING", "Menghapus klip privat…")
        val ticket = diagnosticOperation.begin(sheetOwner, sheetState, captureGeneration)
        val deleted = diagnostic.dismiss()
        lifecycle.execute {
            val stopped = runCatching { deleted.get(2, TimeUnit.SECONDS); true }.getOrDefault(false)
            main.post {
                if (!destroyed && diagnosticOperation.accepts(ticket, sheetOwner, sheetState, captureGeneration))
                    sheet(if (stopped) "READY" else "ERROR", if (stopped) "Klip dihapus. Rekam ulang bila diperlukan." else "Penghapusan klip belum terkonfirmasi. Tutup panel.")
            }
        }
    }
    fun startManualUtterance(): Boolean {
        if (automatic || !ownership.normalAllowed()) return false
        return startCapture("conversation", false)
    }
    fun finishVoiceprintEnrollment(): Boolean = finishCapture()
    fun finishManualUtterance(): Boolean = finishCapture()

    private fun startCapture(purpose: String, auto: Boolean): Boolean {
        if (!permitted() || playbackPending || (purpose == "conversation" && now() - playbackDrainedAt < 600) ||
            startingCapture || audioRecorder?.isRecording() == true || pendingUtterance != null ||
            sealedPending != null || finishPending || (auto && !vadAvailable)) return false
        if (purpose == "conversation" && !ownership.normalAllowed()) return false
        if (purpose != "conversation" && !ownership.dedicatedAllowed(now())) return false
        diagnosticOperation.invalidate(); captureNotice = ""
        if (purpose == "conversation") recovery.remember(webSocketClient?.outdoorSession?.serverInstanceId)
        startingCapture = true
        val gate = ownership.generation()
        val capture = nextCapture()
        val socket = generation
        lifecycle.execute {
            val priorStopped = audioRecorder?.stopAndJoin() != false
            if (!priorStopped) { main.post {
                if (capture == captureGeneration && socket == generation) fail("Mikrofon lama belum berhenti")
            }; return@execute }
            if (capture != captureGeneration || gate != ownership.generation() || !permitted()) return@execute
            vad?.reset()
            local.cancel("new_capture")
            val token = local.arm(purpose, auto, if (auto) vad else null)
            if (!auto) local.start(token, now())
            val recorder = AudioRecorderManager(options = CaptureOptions(automaticVad = auto),
                audioManager = getSystemService(Context.AUDIO_SERVICE) as AudioManager,
                onError = { code -> captureFailed(capture, code) },
                onRecoverableError = { code -> captureFailed(capture, code) },
                onRouteInvalidated = { captureFailed(capture, "route_changed") }) { bytes, count ->
                if (capture == captureGeneration) local.accept(token, bytes, count, now())
            }
            audioRecorder = recorder
            val started = recorder.startRecording()
            if (!started || capture != captureGeneration || gate != ownership.generation() || !permitted()) {
                local.cancel("start_invalidated"); recorder.stopAndJoin()
            }
            main.post {
                if (capture != captureGeneration || socket != generation) return@post
                startingCapture = false
                if (!started || !recorder.isRecording()) { fail("Mikrofon gagal dimulai • tetap Paused"); return@post }
                if (purpose == "conversation") publish(NexoState.LISTENING, if (auto) "Siap • menunggu ucapan" else "Merekam • tekan Selesai", sleepExpression)
                else sheet("RECORDING", if (purpose == "diagnostic") "Merekam klip privat • tekan Selesai" else
                    "Merekam ${if (samples.size == sampleCount) "holdout" else "sampel ${samples.size + 1}"} • tekan Selesai")
                activeToken = token; activePurpose = purpose; activeCaptureGeneration = capture
            }
        }
        return true
    }
    private var activeCaptureGeneration = -1L
    private var activeToken = 0L
    private var activePurpose = "conversation"
    private fun captureFailed(capture: Long, code: String) {
        val revoked = captureAdmission.revoke(capture)
        if (revoked < 0) return
        main.post {
            if (destroyed || revoked != captureGeneration) return@post
            fail("Rekaman gagal: $code")
        }
    }
    private fun invalidateCapture(reason: String) {
        webSocketClient?.fenceCapture()
        nextCapture()
        diagnosticOperation.invalidate()
        recovery.clear(); retryResume = false
        diagnostic.fencePlayback()
        val recorder = audioRecorder
        webSocketClient?.cancelUtterance()
        sealedPending = null; pendingUtterance = null; processing.clear(); finishPending = false; startingCapture = false
        turnComplete = false
        lifecycle.execute {
            local.cancel(reason)
            recorder?.stopAndJoin()
        }
        serviceCallback?.onEnrollmentLevel(0f); serviceCallback?.onTelemetryReceived(0f, null)
    }
    private fun finishCapture(): Boolean {
        if (finishPending || startingCapture || audioRecorder?.isRecording() != true) return false
        if (local.state() !in setOf("CAPTURING", "SEALED")) return false
        finishPending = true
        val capture = captureGeneration
        val token = activeToken
        val purpose = activePurpose
        lifecycle.execute {
            val joined = audioRecorder?.finishAndJoin() == true
            val sealed = if (joined && capture == captureGeneration) local.sealed() ?: local.finish(token, now()) else null
            main.post {
                if (capture != captureGeneration) return@post
                finishPending = false
                if (!joined || sealed == null) { fail("Rekaman tidak utuh • ulangi ucapan"); return@post }
                deliver(sealed, purpose, capture)
            }
        }
        return true
    }
    private fun deliver(sealed: UtteranceBuffer.Sealed, purpose: String, capture: Long) {
        captureNotice = sealed.status("")
        sealedPending = sealed
        if (purpose == "diagnostic") {
            val owner = sheetOwner
            lifecycle.execute {
                val retained = if (capture == captureGeneration && ownership.dedicatedAllowed(now()))
                    runCatching { diagnostic.retain(sealed, true).get(2, TimeUnit.SECONDS) }.getOrDefault(false) else false
                local.release(sealed)
                main.post {
                    if (capture != captureGeneration || owner != sheetOwner) return@post
                    sealedPending = null; diagnosticReady = retained; diagnosticUntil = now() + 300000
                    sheet(if (retained) "LOCAL_READY" else "ERROR", sealed.status(if (retained)
                        "Klip privat siap diputar, dihapus saat ditutup atau setelah 5 menit. Tidak diunggah." else "Klip gagal disimpan lokal."))
                }
            }
            return
        }
        val client = webSocketClient ?: run { fail("Koneksi tidak tersedia"); return }
        if (purpose == "conversation" && client.outdoorSession == null)
            recovery.disconnected(purpose, "SEALED", true)
        val lease = if (purpose == "enrollment") ownership.lease() else null
        val enrollment = enrollmentId
        val deliveryConnection = client.outdoorSession?.connectionId
        val deliveryInstance = recovery.instance()
        pendingUtterance = sealed.id
        processing.begin(sealed.id)
        turnComplete = false
        if (purpose == "conversation") publish(NexoState.THINKING, sealed.status(if (recovery.active())
            "Paused • rekaman selesai lokal, menunggu koneksi dan pemeriksaan status; tidak ada jawaban offline"
            else "Mengirim • menunggu pemeriksaan integritas"), sleepExpression)
        else sheet("UPLOADING", sealed.status("Mengirim sampel ke server • menunggu pemeriksaan integritas"))
        upload.execute {
            val admitted = runCatching {
                if (capture != captureGeneration) false
                else if (purpose == "conversation" && recovery.active()) {
                    val instance = recovery.instance()
                    instance != null && client.retainForRecovery(sealed, instance) { capture == captureGeneration && !destroyed }
                } else client.uploadUtterance(sealed, lease, enrollment, deliveryInstance, deliveryConnection) { capture == captureGeneration && !destroyed }
            }.getOrDefault(false)
            main.post { if (capture == captureGeneration && !admitted) fail("Unggahan tidak diterima • tidak ada fallback audio mentah") }
        }
    }

    private fun control(json: JsonObject) {
        val id = OutdoorProtocol.uuid(json, "connection_id") ?: return
        if (id != ownership.connection()) return
        val type = OutdoorProtocol.string(json, "type") ?: return
        val request = OutdoorProtocol.uuid(json, "request_id") ?: return
        val epoch = OutdoorProtocol.integer(json, "output_epoch", 0, Long.MAX_VALUE)
        when (type) {
            "audio_owner_ack" -> {
                val owner = OutdoorProtocol.uuid(json, "owner_id") ?: return
                val state = OutdoorProtocol.string(json, "state") ?: return
                val paused = json.get("conversation_paused")?.let { it.isJsonPrimitive && it.asJsonPrimitive.isBoolean && it.asBoolean } == true
                if (owner != ownership.owner() || epoch == null) return
                when (state) {
                    "granted" -> {
                        val lease = OutdoorProtocol.uuid(json, "lease_id") ?: return
                        val ttl = OutdoorProtocol.integer(json, "lease_ms", 1, 15000)?.toInt() ?: return
                        ownership.grant(id, request, owner, lease, ttl, paused, epoch, now())
                    }
                    "renewed" -> ownership.renewed(id, request, owner, OutdoorProtocol.uuid(json, "lease_id") ?: return,
                        OutdoorProtocol.integer(json, "lease_ms", 1, 15000)?.toInt() ?: return, now())
                    "released" -> if (ownership.released(id, request, owner, paused, epoch)) {
                        releaseAcknowledged = true; completeReleaseIfReady()
                    }
                    "denied", "expired" -> fail("Kepemilikan mikrofon ditolak atau kedaluwarsa")
                }
            }
            "conversation_resumed" -> {
                val paused = json.get("conversation_paused")?.let { if (it.isJsonPrimitive && it.asJsonPrimitive.isBoolean) it.asBoolean else true } ?: true
                if (epoch == null || !ownership.resumed(id, request, paused, epoch) || !permitted()) return
                if (retryResume) {
                    retryResume = false
                    val capture = captureGeneration
                    webSocketClient?.resumeRetainedUpload { capture == captureGeneration && !destroyed }
                    publish(NexoState.THINKING, "Mengirim • mengulang ID ucapan yang sama; mikrofon dan jawaban audio tetap dijeda", sleepExpression)
                } else startResumedRecorder(id, epoch)
            }
            "enrollment_ack" -> {
                if (!ownership.dedicatedAllowed(now()) || request != enrollmentRequest ||
                    OutdoorProtocol.uuid(json, "owner_id") != sheetOwner || OutdoorProtocol.uuid(json, "lease_id") != ownership.lease() ||
                    OutdoorProtocol.uuid(json, "enrollment_id") != enrollmentId) return
                when (OutdoorProtocol.string(json, "state")) {
                    "collecting" -> if (enrollmentOperation == "begin") {
                        // Additive policy fields are mandatory: do not guess quality/count from RMS or elapsed time.
                        val count = OutdoorProtocol.integer(json, "required_samples", 2, 5)?.toInt()
                        val holdout = OutdoorProtocol.integer(json, "required_holdouts", 1, 1)
                        if (count == null || holdout != 1L) { fail("Server belum mendukung kebijakan beberapa sampel dan holdout"); return }
                        sampleCount = count; enrollmentOperation = null
                        sheet("SAMPLES_READY", "Siap merekam $count sampel dan satu holdout. Penilaian kualitas dilakukan server.")
                    }
                    "saved" -> if (enrollmentOperation == "save") {
                        enrollmentOperation = null
                        sheet("SAVED", "Server mengonfirmasi profil tersimpan. Percakapan tetap Paused.")
                        querySent = false
                        queryVoiceprint()
                    }
                    "rejected", "cancelled" -> fail("Pendaftaran tidak disimpan: ${OutdoorProtocol.string(json, "code") ?: "quality_insufficient"}")
                }
            }
            "utterance_ack" -> {
                val utterance = OutdoorProtocol.uuid(json, "utterance_id") ?: return
                if (pendingUtterance == null || utterance != pendingUtterance) return
                val stage = OutdoorProtocol.string(json, "stage")
                val uploadState = webSocketClient?.uploader?.state()
                if (recovery.active() && stage == "unknown" && uploadState == "RETRY_READY") {
                    publish(NexoState.IDLE, "Paused • ucapan belum diterima. Tekan Mulai untuk kirim ulang dalam 30 detik sejak rekaman selesai", sleepExpression)
                    if (keepVoiceAlive) scheduleAutoRestart()
                    return
                }
                if (webSocketClient?.uploader?.state() in setOf("ACCEPTED", "COMPLETED")) {
                    processing.accepted(pendingUtterance!!, now())
                    sealedPending?.let { sealed -> local.release(sealed) }; sealedPending = null
                    if (recovery.active()) {
                        if (stage == "completed") {
                            pendingUtterance = null; processing.clear()
                            pauseLocal("recovered_completed")
                            publish(NexoState.IDLE, "Paused • server sudah menyelesaikan ucapan; tidak dikirim ulang. Tekan Mulai untuk ucapan baru", sleepExpression)
                            if (keepVoiceAlive) scheduleAutoRestart()
                        } else publish(NexoState.THINKING, "Paused • ucapan diterima, menunggu hasil server tanpa membuka mikrofon atau jawaban audio", sleepExpression)
                    } else if (sheetOwner == null) publish(NexoState.THINKING, "Memahami • audio diterima utuh oleh server", sleepExpression)
                    else sheet("PROCESSING", "Memeriksa sampel di server • level sinyal bukan bukti kualitas")
                }
            }
            "utterance_result" -> {
                val utterance = OutdoorProtocol.uuid(json, "utterance_id") ?: return
                if (utterance != pendingUtterance) return
                if (!processing.complete(utterance, now())) { fail("Hasil ucapan terlambat atau belum dikonfirmasi"); return }
                val completed = OutdoorProtocol.string(json, "status") == "completed"
                val code = OutdoorProtocol.string(json, "code") ?: "processing_failed"
                pendingUtterance = null
                sealedPending?.let { sealed -> local.release(sealed) }; sealedPending = null
                if (!completed) {
                    if (sheetOwner == null && !recovery.active() && code in setOf("stt_empty", "no_speech", "speaker_rejected", "quality_insufficient")) {
                        val hint = when (code) {
                            "speaker_rejected" -> "Suara tidak cocok dengan pemilik terdaftar"
                            "quality_insufficient" -> "Suara terlalu singkat atau kurang jelas"
                            else -> "Suara belum terdengar jelas • silakan bicara lagi"
                        }
                        serviceCallback?.onTranscriptionReceived("system", hint, true)
                        turnComplete = true; maybeNextTurn(); return
                    }
                    if (sheetOwner == null && !recovery.active() && code in setOf("stt_failed", "hermes_failed", "tts_failed", "processing_failed")) {
                        playbackPending = false; audioPlayer.stop()
                        serviceCallback?.onTranscriptionReceived("system", "Kendala layanan sementara ($code) • silakan bicara lagi", true)
                        turnComplete = true; maybeNextTurn(); return
                    }
                    if (sheetOwner != null && code in setOf("no_speech", "quality_insufficient", "invalid_embedding")) {
                        sheet(if (samples.isEmpty()) "READY" else "SAMPLES_READY", "Suara kurang jelas ($code). Ulangi sampel dengan satu kalimat jelas.")
                        return
                    }
                    fail("Ucapan gagal: $code"); return
                }
                if (sheetOwner != null) {
                    samples.add(utterance)
                    sheet("SAMPLES_READY", if (samples.size > sampleCount) "Sampel dan holdout diterima. Tekan Simpan untuk validasi dan publikasi profil." else
                        "Sampel diterima. Berikutnya: ${if (samples.size == sampleCount) "holdout terpisah" else "ucapan berbeda"}.")
                } else if (recovery.active()) {
                    pauseLocal("recovered_result")
                    publish(NexoState.IDLE, "Paused • hasil ucapan selesai; tekan Mulai untuk ucapan baru", sleepExpression)
                    if (keepVoiceAlive) scheduleAutoRestart()
                } else { turnComplete = true; maybeNextTurn() }
            }
            "utterance_error" -> if (OutdoorProtocol.uuid(json, "utterance_id") == pendingUtterance) fail("Unggahan ditolak")
        }
    }
    private fun startResumedRecorder(id: String, epoch: Long) {
        captureNotice = ""
        recovery.remember(webSocketClient?.outdoorSession?.serverInstanceId)
        val gate = ownership.generation()
        val capture = nextCapture()
        startingCapture = true
        audioPlayer.release(); audioPlayer = newPlayer()
        lifecycle.execute {
            if (audioRecorder?.stopAndJoin() == false) { main.post {
                if (capture == captureGeneration && gate == ownership.generation()) fail("Mikrofon lama belum berhenti")
            }; return@execute }
            if (gate != ownership.generation() || capture != captureGeneration || !permitted()) return@execute
            vad?.reset(); local.cancel("resume")
            val useAuto = automatic && vad != null
            val token = local.arm("conversation", useAuto, if (useAuto) vad else null)
            if (!useAuto) local.start(token, now())
            val recorder = AudioRecorderManager(options = CaptureOptions(automaticVad = automatic),
                audioManager = getSystemService(Context.AUDIO_SERVICE) as AudioManager,
                onError = { captureFailed(capture, it) }, onRecoverableError = { captureFailed(capture, it) },
                onRouteInvalidated = { captureFailed(capture, "route_changed") }) { bytes, count ->
                if (capture == captureGeneration) local.accept(token, bytes, count, now())
            }
            audioRecorder = recorder
            val started = recorder.startRecording()
            if (capture != captureGeneration || gate != ownership.generation()) { local.cancel("stale_resume"); recorder.stopAndJoin(); return@execute }
            main.post {
                if (capture != captureGeneration || gate != ownership.generation()) return@post
                startingCapture = false
                if (!ownership.recorderReady(gate, started && recorder.isRecording()) || !permitted()) { fail("Mikrofon gagal dimulai"); return@post }
                webSocketClient?.allowOutput(id, epoch)
                autoRestartAt = 0L
                activeToken = token; activePurpose = "conversation"; activeCaptureGeneration = capture
                publish(NexoState.LISTENING, if (automatic) "Siap • menunggu ucapan" else "Merekam • tekan Selesai", sleepExpression)
            }
        }
    }
    private fun output(json: JsonObject) {
        val id = OutdoorProtocol.uuid(json, "connection_id") ?: return
        val epoch = OutdoorProtocol.integer(json, "output_epoch", 0, Long.MAX_VALUE) ?: return
        if (!ownership.outputAllowed(id, epoch)) return
        processing.activity(now())
        when (OutdoorProtocol.string(json, "type")) {
            "tts_start" -> {
                startingCapture = false
                playbackPending = true; playbackTurn.incrementAndGet()
                earconManager.stop()
                if (playbackReady) audioPlayer.start()
                publish(NexoState.SPEAKING, "Menjawab • mikrofon dijeda", currentExpression)
            }
            "tts_end" -> {
                val turn = playbackTurn.get()
                audioPlayer.finish { main.post {
                    if (ownership.outputAllowed(id, epoch) && turn == playbackTurn.get()) {
                        playbackPending = false; playbackDrainedAt = now(); maybeNextTurn()
                    }
                } }
            }
            "state_change", "cognitive" -> {
                val expression = OutdoorProtocol.string(json, "expression")
                if (expression == "sleeping" || expression == "tidur") sleepExpression = expression
                else if (expression != null) sleepExpression = null
                when (OutdoorProtocol.string(json, "state")) {
                    "THINKING" -> publish(NexoState.THINKING, "Memahami • mikrofon dijeda", expression)
                    "SPEAKING" -> publish(NexoState.SPEAKING, "Menjawab • mikrofon dijeda", expression)
                    "ERROR" -> fail("Server gagal memproses ucapan")
                    // IDLE/LISTENING are not permission to arm hardware or publish ready.
                }
            }
            "transcription" -> serviceCallback?.onTranscriptionReceived(OutdoorProtocol.string(json, "role") ?: "assistant",
                OutdoorProtocol.string(json, "text") ?: "", json.get("is_final")?.asBoolean ?: true)
        }
    }
    private fun maybeNextTurn() {
        if (!turnComplete || playbackPending || !ownership.normalAllowed() || now() - playbackDrainedAt < 600) return
        if (automatic && vadAvailable && (startingCapture || audioRecorder?.isRecording() == true || pendingUtterance != null || sealedPending != null || finishPending)) return
        turnComplete = false
        if (automatic && vadAvailable) startCapture("conversation", true)
        else publish(NexoState.IDLE, "Siap • tekan Mulai bicara untuk ucapan berikutnya", sleepExpression)
    }
    private val tick = object : Runnable {
        override fun run() {
            if (destroyed) return
            val time = now()
            if (!permitted() && (sheetOwner != null || voiceEnabled || recovery.active())) { fail("Izin atau tampilan mikrofon tidak aktif") }
            if (sheetOwner != null && sheetState == "RELEASING" && time >= releaseDeadline) fail("Pelepasan mikrofon belum terkonfirmasi")
            else if (sheetOwner != null && sheetState == "ACQUIRING" && time - sheetOpened >= 3000) fail("Batas waktu akuisisi mikrofon")
            else if (ownership.expired(time)) fail("Lease atau konfirmasi mikrofon kedaluwarsa")
            else if (sheetOwner != null && !ownership.releasing()) {
                if (ownership.renewDue(time)) {
                    val request = uuid()
                    if (ownership.renew(request, time) && webSocketClient?.sendOutdoorControl("audio_owner_renew", request,
                            mapOf("owner_id" to sheetOwner!!, "lease_id" to ownership.lease()!!)) != true) fail("Pembaruan lease gagal")
                }
                if (sheetState == "ACQUIRING" && sheetLocalStopped && ownership.dedicatedAllowed(time)) {
                    sheet("READY", if (sheetPurpose == "diagnostic") "Siap • rekam klip lokal hanya dengan persetujuan Anda" else "Siap • memuat status profil, percakapan tetap Paused")
                    queryVoiceprint()
                }
            }
            if (queryRequest != null && time >= queryDeadline) fail("Status profil tidak tersedia")
            if (pendingDelete != null && time >= deleteDeadline) fail("Hasil penghapusan belum diketahui • periksa pada pembukaan berikutnya")
            if (enrollmentOperation != null && time >= enrollmentDeadline) fail("Pendaftaran melewati batas waktu")
            if (audioRecorder?.isRecording() == true && !finishPending && !startingCapture) {
                when (local.state()) {
                    "SEALED" -> finishCapture()
                    "ERROR", "PAUSED" -> fail("Rekaman gagal: ${local.error() ?: "capture_failed"}")
                    "CAPTURING", "ARMED" -> {
                        val rms = local.metrics().rms().toFloat().coerceIn(0f, 1f)
                        if (sheetOwner != null) serviceCallback?.onEnrollmentLevel(rms)
                        else {
                            serviceCallback?.onTelemetryReceived(rms, null)
                            if (local.state() == "CAPTURING") publish(NexoState.LISTENING, if (recovery.active())
                                "Merekam lokal • tekan Selesai, maksimal 20 detik; percakapan dan jawaban audio tetap dijeda"
                                else if (automatic) "Mendengarkan ucapan…" else "Merekam • tekan Selesai", sleepExpression)
                        }
                    }
                }
            }
            if (processing.expired(time)) fail("Pemeriksaan ucapan melewati batas waktu • percakapan tetap Paused, lanjutkan secara eksplisit untuk mencoba ulang")
            val uploadState = webSocketClient?.uploader?.state()
            if (pendingUtterance != null && uploadState in setOf("ERROR", "EXPIRED", "DELIVERY_UNKNOWN"))
                fail("Unggahan terhenti: ${webSocketClient?.uploader?.error() ?: "upload_failed"} • lanjutkan secara eksplisit untuk ucapan baru")
            else if (pendingUtterance != null && uploadState == "DISCONNECTED" && !recovery.active()) transientDisconnect()
            if (diagnosticReady && time >= diagnosticUntil) { diagnosticReady = false; sheet("READY", "Klip kedaluwarsa dan dihapus. Rekam ulang bila diperlukan.") }
            maybeNextTurn()
            if (keepVoiceAlive && permitted() && sheetOwner == null) {
                if (autoRestartAt in 1..time) {
                    if (startingCapture || finishPending || audioRecorder?.isRecording() == true || ownership.normalAllowed()) {
                        autoRestartAt = 0L
                    } else if (recovery.active()) {
                        autoRestartAt = time + 2000L
                        if (webSocketClient == null) ensureConnection()
                        else if (webSocketClient?.uploader?.state() == "RETRY_READY") startVoice()
                    } else if (webSocketClient != null && webSocketClient?.outdoorSession == null) {
                        resumeOnHello = true
                        autoRestartAt = time + 2000L
                    } else {
                        startVoice()
                        autoRestartAt = time + 2500L
                    }
                } else if (ownership.normalAllowed() && automatic && vadAvailable && !turnComplete &&
                    !playbackPending && !startingCapture && !finishPending && pendingUtterance == null &&
                    sealedPending == null && audioRecorder?.isRecording() != true && time - playbackDrainedAt >= 600) {
                    startCapture("conversation", true)
                }
            }
            main.postDelayed(this, 100)
        }
    }
    private fun startForegroundNotification() {
        val channelId = NOTIFICATION_CHANNEL_ID
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        manager.createNotificationChannel(NotificationChannel(channelId, "Nexo Appliance Service", NotificationManager.IMPORTANCE_LOW))
        val notification = NotificationCompat.Builder(this, channelId).setContentTitle("Nexo Appliance")
            .setContentText("Kontrol audio Nexo • lihat layar untuk status mikrofon")
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentIntent(PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)).build()
        try { startForeground(NOTIFICATION_ID, notification) }
        catch (_: SecurityException) { foregroundAllowed = false; persistOff(); publish(NexoState.ERROR, "Izin layanan mikrofon diperlukan") }
        catch (_: IllegalStateException) { foregroundAllowed = false; persistOff(); publish(NexoState.ERROR, "Buka aplikasi untuk memakai mikrofon") }
    }

    private var mediaSession: MediaSession? = null
    private fun ensureMediaSession(): MediaSession {
        var session = mediaSession
        if (session == null) {
            session = MediaSession(this, "NexoVoiceSession").apply {
                setCallback(object : MediaSession.Callback() {
                    override fun onStop() { main.post { stopVoice() } }
                    override fun onPause() { main.post { stopVoice() } }
                })
            }
            mediaSession = session
        }
        return session
    }

    private fun updateOngoingNotification(state: NexoState, message: String?, expression: String?) {
        if (!foregroundAllowed || destroyed) return
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager ?: return
        if (!voiceEnabled) {
            mediaSession?.let {
                it.isActive = false
                it.setPlaybackState(
                    PlaybackState.Builder()
                        .setState(PlaybackState.STATE_STOPPED, PlaybackState.PLAYBACK_POSITION_UNKNOWN, 0f)
                        .build()
                )
            }
            val notification = NotificationCompat.Builder(this, NOTIFICATION_CHANNEL_ID)
                .setContentTitle("Nexo Appliance")
                .setContentText("Kontrol audio Nexo • lihat layar untuk status mikrofon")
                .setSmallIcon(android.R.drawable.ic_btn_speak_now)
                .setContentIntent(PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java),
                    PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE))
                .setOngoing(false)
                .setPriority(NotificationCompat.PRIORITY_LOW)
                .build()
            try { manager.notify(NOTIFICATION_ID, notification) } catch (_: Exception) {}
            return
        }

        val session = ensureMediaSession()
        session.isActive = true

        val mascotRes = when {
            expression == "sleeping" || expression == "tidur" -> R.drawable.mascot_sleeping
            state == NexoState.SPEAKING -> R.drawable.mascot_keren
            state == NexoState.THINKING -> R.drawable.mascot_bingung
            state == NexoState.ERROR -> R.drawable.mascot_menangis
            else -> R.drawable.mascot_senang
        }

        val mascotBitmap = runCatching {
            BitmapFactory.decodeResource(resources, mascotRes)
        }.getOrNull()

        val statusText = when {
            state == NexoState.SPEAKING -> "Menjawab ucapan Anda..."
            state == NexoState.THINKING -> "Memahami ucapan..."
            state == NexoState.LISTENING -> if (!uiForeground) "Standby menyimak di latar belakang..." else "Standby mendengarkan suara Anda..."
            else -> message?.takeIf { it.isNotBlank() } ?: "Standby mendengarkan suara Anda..."
        }

        val playbackState = PlaybackState.Builder()
            .setActions(PlaybackState.ACTION_STOP or PlaybackState.ACTION_PAUSE)
            .setState(PlaybackState.STATE_PLAYING, PlaybackState.PLAYBACK_POSITION_UNKNOWN, 1.0f)
            .build()
        session.setPlaybackState(playbackState)

        val metadataBuilder = MediaMetadata.Builder()
            .putString(MediaMetadata.METADATA_KEY_TITLE, "Nexo Appliance")
            .putString(MediaMetadata.METADATA_KEY_ARTIST, statusText)
        if (mascotBitmap != null) {
            metadataBuilder.putBitmap(MediaMetadata.METADATA_KEY_ALBUM_ART, mascotBitmap)
        }
        session.setMetadata(metadataBuilder.build())

        val openAppIntent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP
        }
        val openAppPending = PendingIntent.getActivity(
            this, 101, openAppIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val stopIntent = Intent(this, NexoKioskService::class.java).apply { action = ACTION_STOP_VOICE }
        val stopPending = PendingIntent.getService(
            this, 102, stopIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val stopAction = Notification.Action.Builder(
            Icon.createWithResource(this, R.drawable.ic_mic_off_24),
            "Matikan",
            stopPending
        ).build()

        val notification = Notification.Builder(this, NOTIFICATION_CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle("Nexo Appliance")
            .setContentText(statusText)
            .setLargeIcon(mascotBitmap)
            .setStyle(Notification.MediaStyle().setMediaSession(session.sessionToken).setShowActionsInCompactView(0))
            .addAction(stopAction)
            .setContentIntent(openAppPending)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setVisibility(Notification.VISIBILITY_PUBLIC)
            .build()

        try { manager.notify(NOTIFICATION_ID, notification) } catch (_: Exception) {}
    }

    override fun onDestroy() {
        mediaSession?.release()
        mediaSession = null
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager
        try { manager?.cancel(NOTIFICATION_ID) } catch (_: Exception) {}
        keepVoiceAlive = false; autoRestartAt = 0L
        closeRecordingSheet("service_destroy")
        destroyed = true; ++generation; persistOff()
        ownership.disconnected(); webSocketClient?.fenceOutput()
        invalidateCapture("service_destroy")
        captureAdmission.close()
        main.removeCallbacksAndMessages(null); serviceCallback = null
        earconManager.release(); audioPlayer.release(); webSocketClient?.disconnect(); webSocketClient = null
        lifecycle.execute {
            val joined = audioRecorder?.stopAndJoin() != false
            diagnostic.close()
            // A timed-out consumer may still use ONNX. Do not close/reset its session concurrently.
            if (joined) { vad?.close(); vad = null }
        }
        lifecycle.shutdown(); upload.shutdownNow()
        super.onDestroy()
    }
}
