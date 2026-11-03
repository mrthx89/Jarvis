package id.my.yhdev.nexo.service

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Binder
import android.os.Build
import android.os.IBinder
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import androidx.core.app.NotificationCompat
import id.my.yhdev.nexo.MainActivity
import id.my.yhdev.nexo.config.ServerEndpointPolicy
import id.my.yhdev.nexo.audio.AudioPlayerManager
import id.my.yhdev.nexo.audio.AudioRecorderManager
import id.my.yhdev.nexo.audio.EarconManager
import id.my.yhdev.nexo.audio.HalfDuplexGate
import id.my.yhdev.nexo.network.NexoWebSocketClient
import id.my.yhdev.nexo.network.VoiceprintDialog
import java.util.UUID
import java.util.concurrent.atomic.AtomicInteger
import id.my.yhdev.nexo.ui.NexoState
import id.my.yhdev.nexo.security.DeviceCredentialStore

open class NexoKioskService : Service(), NexoWebSocketClient.WebSocketEventListener {
    private val binder = LocalBinder()
    private val main = Handler(Looper.getMainLooper())
    private val duplex = HalfDuplexGate()
    private lateinit var audioRecorder: AudioRecorderManager
    private lateinit var audioPlayer: AudioPlayerManager
    private lateinit var earconManager: EarconManager
    private var lastListeningChime = 0L
    @Volatile private var webSocketClient: NexoWebSocketClient? = null
    @Volatile private var generation = 0
    @Volatile private var audioGeneration = 0
    private val playbackTurn = AtomicInteger(0)
    private var connectionKey: Pair<String, String>? = null
    private var audioRecovery: Runnable? = null
    private var audioRecoveryAttempts = 0
    private var foregroundAllowed = true
    private val credentialStore by lazy { DeviceCredentialStore(this) }
    private val voicePreferences by lazy { getSharedPreferences("voice_control", Context.MODE_PRIVATE) }
    @Volatile private var voiceEnabled = false
    val isVoiceEnabled: Boolean
        get() = voiceEnabled
    private var currentState = NexoState.IDLE
    private var currentMessage: String? = "Suara dijeda • tekan Mulai untuk mendengarkan"
    private var currentExpression: String? = null
    private var lastVoiceprintDialog: VoiceprintDialog? = null
    private enum class EnrollmentPhase { STARTING, RECORDING, SAVING, CANCELLING }
    private val enrollmentLock = Any()
    @Volatile private var pendingEnrollmentId: String? = null
    @Volatile private var enrollmentPhase: EnrollmentPhase? = null
    private var enrollmentGeneration = -1
    @Volatile private var voiceprintConnectionReady = false
    private var pendingUnenrollId: String? = null
    private var enrollmentTimeout: Runnable? = null
    private var enrollmentSilencedChimes = false
    private val enrollmentActive: Boolean
        get() = pendingEnrollmentId != null

    var serviceCallback: ServiceCallback? = null
        set(value) {
            field = value
            value?.onVoiceEnabledChanged(isVoiceEnabled)
            value?.onStateChanged(currentState, currentMessage, currentExpression)
            lastVoiceprintDialog?.let { value?.onVoiceprintDialogReceived(it) }
        }

    interface ServiceCallback {
        fun onVoiceEnabledChanged(enabled: Boolean)
        fun onStateChanged(state: NexoState, message: String?, expression: String?)
        fun onTelemetryReceived(amplitude: Float, bands: List<Float>?)
        fun onEnrollmentLevel(amplitude: Float) {}
        fun onTranscriptionReceived(role: String, text: String, isFinal: Boolean)
        fun onVoiceprintDialogReceived(dialog: id.my.yhdev.nexo.network.VoiceprintDialog)
    }

    inner class LocalBinder : Binder() {
        fun getService(): NexoKioskService = this@NexoKioskService
    }

    override fun onBind(intent: Intent?): IBinder = binder

    private fun publish(state: NexoState, message: String?, expression: String? = null) {
        currentState = state
        currentMessage = message
        currentExpression = expression
        serviceCallback?.onStateChanged(state, message, expression)
        startForegroundNotification()
    }

    private fun publishListening() {
        if (enrollmentActive) return
        if (isVoiceEnabled && duplex.canSendAudio) {
            val now = SystemClock.uptimeMillis()
            val isSleeping = currentExpression == "sleeping"
            if (!isSleeping && now - lastListeningChime > 1500L && ::earconManager.isInitialized) {
                lastListeningChime = now
                earconManager.playListening()
            }
            val expr = if (isSleeping) "sleeping" else "netral"
            val msg = if (isSleeping) "Mode tidur • ucapkan 'Nexo bangun'" else "Mikrofon aktif • silakan bicara"
            publish(NexoState.LISTENING, msg, expr)
        }
    }

    // Called by the visible Activity after microphone permission has been granted.
    fun startVoice() {
        if (isVoiceEnabled && webSocketClient != null &&
            (::audioRecorder.isInitialized && audioRecorder.isRecording() || enrollmentActive)) return
        cancelAudioRecovery()
        audioRecoveryAttempts = 0
        voicePreferences.edit().putBoolean("enabled", true).commit()
        voiceEnabled = true
        serviceCallback?.onVoiceEnabledChanged(true)
        foregroundAllowed = true // Explicit visible user action may retry OS promotion.
        startForegroundNotification()
        if (!foregroundAllowed) return
        // Update the started-service restart policy after an OFF -> ON binder call.
        try {
            startService(Intent(this, NexoKioskService::class.java))
        } catch (e: SecurityException) {
            foregroundDenied()
            return
        } catch (e: IllegalStateException) {
            foregroundDenied()
            return
        }
        connectionKey?.let { initializeConnection(it.first, it.second, forceReconnect = true) }
            ?: publish(NexoState.DISCONNECTED, "Menunggu endpoint server...")
    }

    fun stopVoice() {
        cancelAudioRecovery()
        audioRecoveryAttempts = 0
        voicePreferences.edit().putBoolean("enabled", false).commit()
        voiceEnabled = false
        ++generation
        ++audioGeneration
        duplex.disconnected()
        invalidateVoiceprint("Sesi suara berakhir • status suara belum diketahui")
        audioRecorder.stopRecording()
        audioPlayer.stop()
        webSocketClient?.disconnect()
        webSocketClient = null
        serviceCallback?.onVoiceEnabledChanged(false)
        publish(NexoState.IDLE, "Suara dijeda • tekan Mulai untuk mendengarkan", "netral")
        startForegroundNotification()
        stopSelf() // OFF must also remove the old START_STICKY started-service request.
    }

    override fun onCreate() {
        super.onCreate()
        earconManager = EarconManager(this)
        voiceEnabled = voicePreferences.getBoolean("enabled", false)
        startForegroundNotification()
        createAudioSession()
        if (!foregroundAllowed) return
        if (isVoiceEnabled) publish(NexoState.DISCONNECTED, "Menunggu endpoint server...")
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // A sticky recreation has no Activity to supply Remote Config or device ID.
        // Android owns whether a microphone FGS restart is allowed; never schedule resurrection.
        if (!foregroundAllowed) return START_NOT_STICKY
        if (!isVoiceEnabled) return START_NOT_STICKY
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            failAudioSession("Izin mikrofon diperlukan • buka aplikasi untuk melanjutkan")
            stopSelf(startId)
            return START_NOT_STICKY
        }
        if (connectionKey == null) connectionKey = restoreConnectionKey()
        connectionKey?.let { initializeConnection(it.first, it.second) }
            ?: publish(NexoState.ERROR, "Buka aplikasi untuk memuat endpoint suara")
        return START_STICKY
    }

    private fun restoreConnectionKey(): Pair<String, String>? {
        val url = voicePreferences.getString("server_url", null) ?: return null
        val id = voicePreferences.getString("device_id", null) ?: return null
        return if (ServerEndpointPolicy.isValid(url) && id.isNotBlank()) Pair(url, id) else null
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        // Home/task swipe is not Force stop. Keep the existing started FGS only.
        // No alarms, jobs, receiver, or background Activity/service relaunch here.
        super.onTaskRemoved(rootIntent)
    }

    private fun createAudioSession() {
        // Capture the session token at construction, never when a late error arrives.
        val audioToken = ++audioGeneration
        val client = webSocketClient
        if (::audioRecorder.isInitialized) audioRecorder.stopRecording()
        if (::audioPlayer.isInitialized) audioPlayer.release()
        audioPlayer = AudioPlayerManager(onError = { message ->
            main.post {
                if (!isVoiceEnabled || audioToken != audioGeneration) return@post
                failAudioSession(message)
            }
        })
        audioRecorder = AudioRecorderManager(onError = { message ->
            main.post {
                if (!isVoiceEnabled || audioToken != audioGeneration) return@post
                failAudioSession(message)
            }
        }, onRecoverableError = { message ->
            main.post {
                if (isVoiceEnabled && audioToken == audioGeneration) recoverAudioSession(message)
            }
        }) { chunk, length ->
            // Serialize PCM with enrollment controls: no chunk can cross start/finish/cancel.
            // Use this session's socket, never a replacement endpoint's socket.
            synchronized(enrollmentLock) {
                if (isVoiceEnabled && audioToken == audioGeneration) {
                    val requestId = pendingEnrollmentId
                    val enrollmentRecording = requestId != null && enrollmentPhase == EnrollmentPhase.RECORDING
                    val normalRecording = requestId == null &&
                        isVoiceEnabled && audioToken == audioGeneration && duplex.canSendAudio
                    if (enrollmentRecording || normalRecording) {
                        val localAmp = calculateNormalizedRms(chunk, length)
                        main.post {
                            if (isVoiceEnabled && audioToken == audioGeneration) {
                                if (enrollmentRecording && pendingEnrollmentId == requestId &&
                                    enrollmentPhase == EnrollmentPhase.RECORDING) {
                                    serviceCallback?.onEnrollmentLevel(localAmp)
                                } else if (normalRecording && !enrollmentActive && duplex.canSendAudio) {
                                    serviceCallback?.onTelemetryReceived(localAmp, null)
                                }
                            }
                        }
                        if (client?.sendAudioChunk(chunk, length) != true) {
                            val token = generation
                            main.post {
                                if (isVoiceEnabled && token == generation && audioToken == audioGeneration) {
                                    failAudioSession("Pengiriman audio gagal • coba hubungkan ulang")
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    private fun calculateNormalizedRms(chunk: ByteArray, length: Int): Float {
        val numSamples = length / 2
        if (numSamples <= 0) return 0f
        var sumSquares = 0.0
        var i = 0
        while (i < length - 1) {
            val low = chunk[i].toInt() and 0xFF
            val high = chunk[i + 1].toInt()
            val sample = (high shl 8) or low
            sumSquares += (sample * sample).toDouble()
            i += 2
        }
        val rms = Math.sqrt(sumSquares / numSamples)
        if (rms <= 1.0) return 0f
        val db = 20.0 * Math.log10(rms / 32767.0)
        return Math.max(0.0, Math.min(1.0, (db + 55.0) / 40.0)).toFloat()
    }

    private fun cancelAudioRecovery() {
        audioRecovery?.let { main.removeCallbacks(it) }
        audioRecovery = null
    }

    private fun recoverAudioSession(message: String) {
        if (!isVoiceEnabled || audioRecovery != null) return
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED ||
            audioRecoveryAttempts >= 3 || connectionKey == null) {
            failAudioSession(message)
            return
        }
        // A fresh handshake prevents old turn/audio callbacks reopening the gate.
        failAudioSession(message)
        if (!foregroundAllowed) return
        val token = generation
        val delayMs = 1000L shl audioRecoveryAttempts++ // 1, 2, 4 seconds; never a tight loop.
        audioRecovery = Runnable {
            audioRecovery = null
            if (isVoiceEnabled && token == generation) {
                connectionKey?.let { initializeConnection(it.first, it.second, forceReconnect = true) }
            }
        }
        publish(NexoState.ERROR, "$message • mencoba memulihkan mikrofon")
        if (!foregroundAllowed) return
        audioRecovery?.let { main.postDelayed(it, delayMs) }
    }

    private fun failAudioSession(message: String) {
        cancelAudioRecovery()
        // Permission/configuration/output failures remain terminal; STOP is still available.
        ++generation
        ++audioGeneration
        duplex.disconnected()
        invalidateVoiceprint("Sesi suara berakhir • status suara belum diketahui")
        audioRecorder.stopRecording()
        audioPlayer.stop()
        webSocketClient?.disconnect()
        webSocketClient = null
        publish(NexoState.ERROR, message)
    }

    fun refreshCredentials() {
        if (!isVoiceEnabled) return
        connectionKey?.let { initializeConnection(it.first, it.second, forceReconnect = true) }
    }

    fun initializeConnection(serverUrl: String, deviceId: String, forceReconnect: Boolean = false) {
        val sameEndpoint = connectionKey == Pair(serverUrl, deviceId)
        if (!ServerEndpointPolicy.isValid(serverUrl) || deviceId.isBlank()) {
            failAudioSession("Endpoint suara tidak valid")
            return
        }
        connectionKey = Pair(serverUrl, deviceId)
        voicePreferences.edit().putString("server_url", serverUrl).putString("device_id", deviceId).apply()
        // Remote Config may change while paused; remember it without opening credentials or audio.
        if (!isVoiceEnabled) return
        if (!foregroundAllowed) return
        if (!forceReconnect && sameEndpoint && webSocketClient != null) return
        cancelAudioRecovery()
        val bearerToken = credentialStore.read(serverUrl, deviceId)
        val token = ++generation
        ++audioGeneration
        duplex.disconnected()
        invalidateVoiceprint("Sesi suara berakhir • status suara belum diketahui")
        audioRecorder.stopRecording()
        audioPlayer.stop()
        webSocketClient?.disconnect()
        webSocketClient = null
        if (bearerToken == null) {
            publish(NexoState.ERROR, "Penyiapan perangkat diperlukan • tekan lama status untuk token pemilik")
            return
        }
        publish(NexoState.DISCONNECTED, "Menghubungkan ke server...")
        if (!foregroundAllowed) return
        // Fence callbacks from the old endpoint during Remote Config changes.
        val events = object : NexoWebSocketClient.WebSocketEventListener {
            private fun dispatch(action: () -> Unit) { main.post { if (isVoiceEnabled && token == generation) action() } }
            override fun onConnected() = dispatch { this@NexoKioskService.onConnected() }
            override fun onDisconnected() = dispatch { this@NexoKioskService.onDisconnected() }
            override fun onStateChanged(state: NexoState, message: String?, expression: String?) =
                dispatch { this@NexoKioskService.onStateChanged(state, message, expression) }
            override fun onTelemetryReceived(amplitude: Float, bands: List<Float>?) =
                dispatch { this@NexoKioskService.onTelemetryReceived(amplitude, bands) }
            override fun onTranscriptionReceived(role: String, text: String, isFinal: Boolean) =
                dispatch { this@NexoKioskService.onTranscriptionReceived(role, text, isFinal) }
            override fun onAudioChunkReceived(audioData: ByteArray) {
                if (isVoiceEnabled && token == generation) this@NexoKioskService.onAudioChunkReceived(audioData)
            }
            override fun onTTSStarted() {
                if (isVoiceEnabled && token == generation) this@NexoKioskService.onTTSStarted()
            }
            override fun onTTSEnded() {
                if (isVoiceEnabled && token == generation) this@NexoKioskService.onTTSEnded()
            }
            override fun onVoiceprintDialogReceived(dialog: id.my.yhdev.nexo.network.VoiceprintDialog) =
                dispatch { this@NexoKioskService.onVoiceprintDialogReceived(dialog) }
        }
        webSocketClient = NexoWebSocketClient(serverUrl, deviceId, bearerToken, events)
        webSocketClient?.connect()
    }

    fun startVoiceprintEnrollment(): Boolean {
        if (!isVoiceEnabled || !foregroundAllowed || !voiceprintConnectionReady ||
            checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED ||
            !audioRecorder.isRecording() || !duplex.canSendAudio ||
            currentState == NexoState.THINKING || currentState == NexoState.SPEAKING ||
            (currentState != NexoState.IDLE && currentState != NexoState.LISTENING)) return false
        val client = webSocketClient ?: return false
        val requestId = UUID.randomUUID().toString()
        val token = generation
        synchronized(enrollmentLock) {
            if (enrollmentActive || pendingUnenrollId != null || !duplex.canSendAudio) return false
            pendingEnrollmentId = requestId
            enrollmentGeneration = generation
            enrollmentPhase = EnrollmentPhase.STARTING
            lastVoiceprintDialog = null
            if (::earconManager.isInitialized) earconManager.release()
            enrollmentSilencedChimes = true
            if (!client.startVoiceprintEnrollment(requestId)) {
                // A failed control send leaves server state unknown; do not reopen the mic.
                main.post {
                    if (token == generation && pendingEnrollmentId == requestId)
                        failAudioSession("Pendaftaran suara gagal dikirim • hubungkan ulang")
                }
                return false
            }
        }
        serviceCallback?.onTelemetryReceived(0f, null)
        serviceCallback?.onEnrollmentLevel(0f)
        armEnrollmentTimeout(10_000L)
        return true
    }

    fun finishVoiceprintEnrollment(): Boolean {
        val token = generation
        synchronized(enrollmentLock) {
            val requestId = pendingEnrollmentId ?: return false
            if (enrollmentGeneration != generation || enrollmentPhase != EnrollmentPhase.RECORDING) return false
            enrollmentPhase = EnrollmentPhase.SAVING
            lastVoiceprintDialog = null
            if (webSocketClient?.finishVoiceprintEnrollment(requestId) != true) {
                main.post {
                    if (token == generation && pendingEnrollmentId == requestId)
                        failAudioSession("Penyimpanan suara gagal dikirim • hubungkan ulang")
                }
                return false
            }
        }
        pauseEnrollmentCapture()
        serviceCallback?.onEnrollmentLevel(0f)
        armEnrollmentTimeout(15_000L)
        return true
    }

    fun cancelVoiceprintEnrollment() {
        val token = generation
        synchronized(enrollmentLock) {
            val requestId = pendingEnrollmentId ?: return
            if (enrollmentPhase == EnrollmentPhase.CANCELLING) return
            enrollmentPhase = EnrollmentPhase.CANCELLING
            lastVoiceprintDialog = null
            if (webSocketClient?.cancelVoiceprintEnrollment(requestId) != true) {
                main.post {
                    if (token == generation && pendingEnrollmentId == requestId)
                        failAudioSession("Pembatalan suara gagal dikirim • hubungkan ulang")
                }
                return
            }
        }
        pauseEnrollmentCapture()
        serviceCallback?.onEnrollmentLevel(0f)
        armEnrollmentTimeout(5_000L)
    }

    override fun onVoiceprintDialogReceived(dialog: id.my.yhdev.nexo.network.VoiceprintDialog) {
        if (!isVoiceEnabled || !voiceprintConnectionReady) return
        if (enrollmentActive) {
            if (dialog.requestId != pendingEnrollmentId || enrollmentGeneration != generation) return
            when (dialog.action) {
                "recording" -> {
                    if (enrollmentPhase != EnrollmentPhase.STARTING) return
                    if (dialog.success == false || dialog.available == false) {
                        // Contradictory recording ack: server capture may still be open.
                        cancelVoiceprintEnrollment()
                        return
                    }
                    synchronized(enrollmentLock) { enrollmentPhase = EnrollmentPhase.RECORDING }
                    armEnrollmentTimeout(15_000L)
                }
                "enrolled", "error", "cancelled" -> {
                    // A late save/start reply cannot acknowledge cancellation.
                    if (enrollmentPhase == EnrollmentPhase.CANCELLING && dialog.action != "cancelled") return
                    if (dialog.action == "enrolled" && enrollmentPhase != EnrollmentPhase.SAVING) return
                    completeEnrollment()
                    serviceCallback?.onVoiceprintDialogReceived(dialog)
                    queryVoiceprint()
                    return
                }
                else -> return
            }
        } else if (pendingUnenrollId != null && dialog.requestId == pendingUnenrollId) {
            pendingUnenrollId = null
            lastVoiceprintDialog = null
            serviceCallback?.onVoiceprintDialogReceived(dialog)
            queryVoiceprint()
            return
        } else if (dialog.requestId != null || dialog.action == "recording" ||
            dialog.action == "enrolled" || dialog.action == "cancelled") {
            // Completed, cancelled, or previous-connection replies never revive enrollment.
            return
        }
        lastVoiceprintDialog = dialog
        serviceCallback?.onVoiceprintDialogReceived(dialog)
    }

    private fun pauseEnrollmentCapture() {
        // Do not release enrollment PCM (including a blocked read tail) to conversation.
        duplex.microphoneReady(false)
        audioRecorder.stopRecording()
    }

    private fun completeEnrollment() {
        val wasEnrollment = enrollmentActive
        if (wasEnrollment) pauseEnrollmentCapture()
        enrollmentTimeout?.let { main.removeCallbacks(it) }
        enrollmentTimeout = null
        if (enrollmentSilencedChimes) {
            earconManager = EarconManager(this)
            enrollmentSilencedChimes = false
        }
        synchronized(enrollmentLock) {
            pendingEnrollmentId = null
            enrollmentPhase = null
            enrollmentGeneration = -1
        }
        lastVoiceprintDialog = null
        serviceCallback?.onEnrollmentLevel(0f)
        serviceCallback?.onTelemetryReceived(0f, null)
        if (wasEnrollment && isVoiceEnabled && voiceprintConnectionReady) {
            publish(NexoState.IDLE, "Pendaftaran selesai • tekan Mulai untuk melanjutkan mikrofon", "netral")
        }
    }

    private fun invalidateVoiceprint(message: String) {
        voiceprintConnectionReady = false
        val requestId = pendingEnrollmentId
        pendingUnenrollId = null
        completeEnrollment()
        lastVoiceprintDialog = null
        // Never replay old biometric status after STOP, endpoint change, or reconnect.
        serviceCallback?.onVoiceprintDialogReceived(VoiceprintDialog(
            action = "error", requestId = requestId, success = false,
            available = null, message = message
        ))
    }

    private fun armEnrollmentTimeout(delayMs: Long) {
        enrollmentTimeout?.let { main.removeCallbacks(it) }
        val requestId = pendingEnrollmentId ?: return
        val token = generation
        val phase = enrollmentPhase
        enrollmentTimeout = Runnable {
            if (requestId == pendingEnrollmentId && token == generation && phase == enrollmentPhase) {
                if (phase == EnrollmentPhase.RECORDING || phase == EnrollmentPhase.SAVING) {
                    cancelVoiceprintEnrollment()
                } else {
                    // No start/cancel acknowledgement: tear down the socket before reopening PCM.
                    failAudioSession("Pendaftaran suara melewati batas waktu • hubungkan ulang")
                }
            }
        }.also { main.postDelayed(it, delayMs) }
    }

    fun queryVoiceprint() {
        if (isVoiceEnabled && voiceprintConnectionReady && !enrollmentActive) webSocketClient?.queryVoiceprint()
    }

    fun unenrollVoiceprint() {
        if (!isVoiceEnabled || !voiceprintConnectionReady || enrollmentActive) return
        val requestId = UUID.randomUUID().toString()
        pendingUnenrollId = requestId
        if (webSocketClient?.unenrollVoiceprint(requestId) != true) {
            pendingUnenrollId = null
            failAudioSession("Penghapusan suara gagal dikirim • hubungkan ulang")
        }
    }

    private fun foregroundDenied() {
        foregroundAllowed = false
        cancelAudioRecovery()
        ++generation
        ++audioGeneration
        duplex.disconnected()
        invalidateVoiceprint("Sesi suara berakhir • status suara belum diketahui")
        if (::audioRecorder.isInitialized) audioRecorder.stopRecording()
        if (::audioPlayer.isInitialized) audioPlayer.stop()
        webSocketClient?.disconnect()
        webSocketClient = null
        currentState = NexoState.ERROR
        currentMessage = "Android membatasi mikrofon latar belakang • buka aplikasi untuk melanjutkan"
        currentExpression = null
        serviceCallback?.onStateChanged(currentState, currentMessage, currentExpression)
        stopSelf() // Do not retry denied background microphone startup or change saved ON/OFF.
    }

    private fun startForegroundNotification() {
        if (!foregroundAllowed) return
        val channelId = "nexo_kiosk_service"
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(channelId, "Nexo Appliance Service", NotificationManager.IMPORTANCE_LOW)
            (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).createNotificationChannel(channel)
        }
        val status = when {
            !isVoiceEnabled -> "Mikrofon nonaktif • suara dijeda"
            ::audioRecorder.isInitialized && audioRecorder.isRecording() ->
                "Mikrofon aktif • lihat layar untuk status percakapan"
            else -> "Suara diaktifkan • mikrofon belum aktif"
        }
        val notification: Notification = NotificationCompat.Builder(this, channelId)
            .setContentTitle("Nexo Appliance")
            .setContentText(status)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentIntent(PendingIntent.getActivity(this, 0,
                Intent(this, MainActivity::class.java), PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE))
            .setOngoing(isVoiceEnabled)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
        try {
            startForeground(1001, notification)
        } catch (e: SecurityException) {
            Log.w("NexoKioskService", "Microphone foreground permission denied")
            foregroundDenied()
        } catch (e: IllegalStateException) {
            Log.w("NexoKioskService", "Foreground startup not allowed in current app state")
            foregroundDenied()
        }
    }

    override fun onConnected() {
        if (!isVoiceEnabled) return
        if (audioRecorder.isRecording()) return // Duplicate hello must not chop capture or playback.
        voiceprintConnectionReady = true
        // The client's onConnected is server_hello acknowledgement, not raw socket open.
        createAudioSession()
        duplex.connected()
        val started = audioRecorder.startRecording()
        duplex.microphoneReady(started)
        if (started) {
            webSocketClient?.queryVoiceprint()
            val wasDisconnected = currentState == NexoState.DISCONNECTED
            if (wasDisconnected) {
                // Joyful reconnect greeting before settling into neutral listening
                publish(NexoState.LISTENING, "Terhubung kembali! • Nexo Siaga", "senang")
                main.postDelayed({
                    if (isVoiceEnabled && !enrollmentActive && audioRecorder.isRecording() && currentState == NexoState.LISTENING && currentExpression == "senang") {
                        publishListening()
                    }
                }, 2500L)
            } else {
                publishListening()
            }
            val audioToken = audioGeneration
            main.postDelayed({
                if (isVoiceEnabled && audioToken == audioGeneration && audioRecorder.isRecording()) {
                    audioRecoveryAttempts = 0
                }
            }, 30_000L)
        }
        // startRecording reports the typed failure exactly once via its callback.
    }

    override fun onDisconnected() {
        if (!isVoiceEnabled) return
        ++audioGeneration
        duplex.disconnected()
        invalidateVoiceprint("Sesi suara berakhir • status suara belum diketahui")
        audioRecorder.stopRecording()
        audioPlayer.stop()
        publish(NexoState.DISCONNECTED, "Koneksi terputus • mencari sinyal server...", "bingung")
    }

    override fun onStateChanged(state: NexoState, message: String?, expression: String?) {
        if (!isVoiceEnabled) return
        if (state == NexoState.ERROR) {
            failAudioSession(message ?: "Sesi suara gagal")
            return
        }
        if (state == NexoState.DISCONNECTED) {
            ++audioGeneration
            duplex.disconnected()
            invalidateVoiceprint(message ?: "Koneksi suara terputus")
            audioRecorder.stopRecording()
            audioPlayer.stop()
        }
        if (enrollmentActive || audioRecovery != null) return
        if (state == NexoState.THINKING && !duplex.playbackPending && ::earconManager.isInitialized) {
            earconManager.playThinking()
        }
        duplex.stateChanged(state)
        if (duplex.playbackPending && (state == NexoState.IDLE || state == NexoState.LISTENING)) {
            publish(NexoState.SPEAKING, "Menyelesaikan pemutaran • mikrofon dijeda", currentExpression)
        } else if (state == NexoState.IDLE || state == NexoState.LISTENING) {
            if (audioRecorder.isRecording()) publishListening()
            else publish(NexoState.ERROR, "Mikrofon tidak aktif. Periksa izin dan perangkat audio.")
        } else {
            publish(state, message ?: when (state) {
                NexoState.THINKING -> "Memproses • mikrofon dijeda"
                NexoState.SPEAKING -> "Berbicara • mikrofon dijeda"
                else -> null
            }, expression)
        }
    }

    override fun onTelemetryReceived(amplitude: Float, bands: List<Float>?) {
        if (!isVoiceEnabled) return
        if (enrollmentActive) return
        serviceCallback?.onTelemetryReceived(amplitude, bands)
    }

    override fun onTranscriptionReceived(role: String, text: String, isFinal: Boolean) {
        if (!isVoiceEnabled) return
        if (enrollmentActive) return
        serviceCallback?.onTranscriptionReceived(role, text, isFinal)
    }

    override fun onAudioChunkReceived(audioData: ByteArray) {
        if (!isVoiceEnabled) return
        if (enrollmentActive) return
        duplex.ttsStarted()
        val token = generation
        val audioToken = audioGeneration
        val player = audioPlayer
        val accepted = player.writeChunk(audioData)
        if (!accepted) main.post {
            if (isVoiceEnabled && token == generation && audioToken == audioGeneration && !enrollmentActive) {
                failAudioSession("Pemutaran audio gagal • hubungkan ulang")
            }
        }
    }

    override fun onTTSStarted() {
        if (!isVoiceEnabled) return
        if (enrollmentActive) return
        val turn = playbackTurn.incrementAndGet()
        val token = generation
        val audioToken = audioGeneration
        duplex.ttsStarted()
        audioPlayer.start()
        main.post {
            if (isVoiceEnabled && token == generation && audioToken == audioGeneration &&
                turn == playbackTurn.get() && !enrollmentActive) {
                earconManager.stop()
                publish(NexoState.SPEAKING, "Berbicara • mikrofon dijeda", currentExpression)
            }
        }
    }

    override fun onTTSEnded() {
        if (!isVoiceEnabled) return
        if (enrollmentActive) return
        val token = generation
        val audioToken = audioGeneration
        val turn = playbackTurn.get()
        audioPlayer.finish {
            main.post {
                if (isVoiceEnabled && token == generation && audioToken == audioGeneration && turn == playbackTurn.get()) {
                    if (enrollmentActive) return@post
                    duplex.playbackDrained()
                    publishListening()
                }
            }
        }
    }

    override fun onDestroy() {
        cancelAudioRecovery()
        main.removeCallbacksAndMessages(null)
        voiceEnabled = false
        ++generation
        ++audioGeneration
        duplex.disconnected()
        invalidateVoiceprint("Sesi suara berakhir • status suara belum diketahui")
        serviceCallback = null
        if (::earconManager.isInitialized) earconManager.release()
        super.onDestroy()
        audioRecorder.stopRecording()
        audioPlayer.release()
        webSocketClient?.disconnect()
        Log.i("NexoKioskService", "NexoKioskService destroyed.")
    }
}
