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

    var serviceCallback: ServiceCallback? = null
        set(value) {
            field = value
            value?.onVoiceEnabledChanged(isVoiceEnabled)
            value?.onStateChanged(currentState, currentMessage, currentExpression)
        }

    interface ServiceCallback {
        fun onVoiceEnabledChanged(enabled: Boolean)
        fun onStateChanged(state: NexoState, message: String?, expression: String?)
        fun onTelemetryReceived(amplitude: Float, bands: List<Float>?)
        fun onTranscriptionReceived(role: String, text: String, isFinal: Boolean)
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
        if (isVoiceEnabled && duplex.canSendAudio) {
            val now = SystemClock.uptimeMillis()
            if (now - lastListeningChime > 1500L && ::earconManager.isInitialized) {
                lastListeningChime = now
                earconManager.playListening()
            }
            publish(NexoState.LISTENING, "Mikrofon aktif • silakan bicara", "netral")
        }
    }

    // Called by the visible Activity after microphone permission has been granted.
    fun startVoice() {
        if (isVoiceEnabled && webSocketClient != null) return
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
            // Continuous PCM for server endpointing. No local voice_end, no barge-in.
            // Use this session's socket, never a replacement endpoint's socket.
            if (isVoiceEnabled && audioToken == audioGeneration && duplex.canSendAudio &&
                client?.sendAudioChunk(chunk, length) != true) {
                val token = generation
                main.post {
                    if (isVoiceEnabled && token == generation && audioToken == audioGeneration) {
                        onDisconnected()
                    }
                }
            }
        }
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
            publish(NexoState.ERROR, "Endpoint suara tidak valid")
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
            override fun onAudioChunkReceived(audioData: ByteArray) =
                dispatch { this@NexoKioskService.onAudioChunkReceived(audioData) }
            override fun onTTSStarted() = dispatch { this@NexoKioskService.onTTSStarted() }
            override fun onTTSEnded() = dispatch { this@NexoKioskService.onTTSEnded() }
        }
        webSocketClient = NexoWebSocketClient(serverUrl, deviceId, bearerToken, events)
        webSocketClient?.connect()
    }

    private fun foregroundDenied() {
        foregroundAllowed = false
        cancelAudioRecovery()
        ++generation
        ++audioGeneration
        duplex.disconnected()
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
        // The client's onConnected is server_hello acknowledgement, not raw socket open.
        createAudioSession()
        duplex.connected()
        val started = audioRecorder.startRecording()
        duplex.microphoneReady(started)
        if (started) {
            publishListening()
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
        audioRecorder.stopRecording()
        audioPlayer.stop()
        publish(NexoState.DISCONNECTED, "Koneksi terputus • mencoba menghubungkan ulang", "sedih")
    }

    override fun onStateChanged(state: NexoState, message: String?, expression: String?) {
        if (!isVoiceEnabled) return
        if (audioRecovery != null) return
        if (state == NexoState.THINKING && ::earconManager.isInitialized) {
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
        serviceCallback?.onTelemetryReceived(amplitude, bands)
    }

    override fun onTranscriptionReceived(role: String, text: String, isFinal: Boolean) {
        if (!isVoiceEnabled) return
        serviceCallback?.onTranscriptionReceived(role, text, isFinal)
    }

    override fun onAudioChunkReceived(audioData: ByteArray) {
        if (!isVoiceEnabled) return
        duplex.ttsStarted()
        val accepted = audioPlayer.writeChunk(audioData)
        if (!accepted) {
            Log.w("NexoKioskService", "Audio buffer queue full; chunk dropped to bound playback latency")
        }
    }

    override fun onTTSStarted() {
        if (!isVoiceEnabled) return
        duplex.ttsStarted()
        publish(NexoState.SPEAKING, "Berbicara • mikrofon dijeda", currentExpression)
        audioPlayer.start()
    }

    override fun onTTSEnded() {
        if (!isVoiceEnabled) return
        val token = generation
        val audioToken = audioGeneration
        audioPlayer.finish {
            main.post {
                if (isVoiceEnabled && token == generation && audioToken == audioGeneration) {
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
        serviceCallback = null
        if (::earconManager.isInitialized) earconManager.release()
        super.onDestroy()
        audioRecorder.stopRecording()
        audioPlayer.release()
        webSocketClient?.disconnect()
        Log.i("NexoKioskService", "NexoKioskService destroyed.")
    }
}
