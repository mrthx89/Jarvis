package id.my.yhdev.jarvis.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Binder
import android.os.Build
import android.os.IBinder
import android.os.Handler
import android.os.Looper
import android.util.Log
import androidx.core.app.NotificationCompat
import id.my.yhdev.jarvis.audio.AudioPlayerManager
import id.my.yhdev.jarvis.audio.AudioRecorderManager
import id.my.yhdev.jarvis.audio.HalfDuplexGate
import id.my.yhdev.jarvis.network.JarvisWebSocketClient
import id.my.yhdev.jarvis.ui.JarvisState
import id.my.yhdev.jarvis.security.DeviceCredentialStore

class JarvisKioskService : Service(), JarvisWebSocketClient.WebSocketEventListener {
    private val binder = LocalBinder()
    private val main = Handler(Looper.getMainLooper())
    private val duplex = HalfDuplexGate()
    private lateinit var audioRecorder: AudioRecorderManager
    private lateinit var audioPlayer: AudioPlayerManager
    @Volatile private var webSocketClient: JarvisWebSocketClient? = null
    @Volatile private var generation = 0
    @Volatile private var audioGeneration = 0
    private var connectionKey: Pair<String, String>? = null
    private val credentialStore by lazy { DeviceCredentialStore(this) }
    private val voicePreferences by lazy { getSharedPreferences("voice_control", Context.MODE_PRIVATE) }
    @Volatile private var voiceEnabled = false
    val isVoiceEnabled: Boolean
        get() = voiceEnabled
    private var currentState = JarvisState.IDLE
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
        fun onStateChanged(state: JarvisState, message: String?, expression: String?)
        fun onTelemetryReceived(amplitude: Float, bands: List<Float>?)
        fun onTranscriptionReceived(role: String, text: String, isFinal: Boolean)
    }

    inner class LocalBinder : Binder() {
        fun getService(): JarvisKioskService = this@JarvisKioskService
    }

    override fun onBind(intent: Intent?): IBinder = binder

    private fun publish(state: JarvisState, message: String?, expression: String? = null) {
        currentState = state
        currentMessage = message
        currentExpression = expression
        serviceCallback?.onStateChanged(state, message, expression)
        startForegroundNotification()
    }

    private fun publishListening() {
        if (isVoiceEnabled && duplex.canSendAudio) {
            publish(JarvisState.LISTENING, "Mikrofon aktif • silakan bicara", "netral")
        }
    }

    // Called by the visible Activity after microphone permission has been granted.
    fun startVoice() {
        if (isVoiceEnabled && webSocketClient != null) return
        voicePreferences.edit().putBoolean("enabled", true).commit()
        voiceEnabled = true
        serviceCallback?.onVoiceEnabledChanged(true)
        connectionKey?.let { initializeConnection(it.first, it.second, forceReconnect = true) }
            ?: publish(JarvisState.DISCONNECTED, "Menunggu endpoint server...")
    }

    fun stopVoice() {
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
        publish(JarvisState.IDLE, "Suara dijeda • tekan Mulai untuk mendengarkan", "netral")
        startForegroundNotification()
    }

    override fun onCreate() {
        super.onCreate()
        voiceEnabled = voicePreferences.getBoolean("enabled", false)
        startForegroundNotification()
        createAudioSession()
        if (isVoiceEnabled) publish(JarvisState.DISCONNECTED, "Menunggu endpoint server...")
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

    private fun failAudioSession(message: String) {
        // Hardware/permission errors require an explicit retry, not a reconnect loop.
        // Keep the user's enabled choice so the Activity can still offer STOP.
        ++generation
        ++audioGeneration
        duplex.disconnected()
        audioRecorder.stopRecording()
        audioPlayer.stop()
        webSocketClient?.disconnect()
        webSocketClient = null
        publish(JarvisState.ERROR, message)
    }

    fun refreshCredentials() {
        if (!isVoiceEnabled) return
        connectionKey?.let { initializeConnection(it.first, it.second, forceReconnect = true) }
    }

    fun initializeConnection(serverUrl: String, deviceId: String, forceReconnect: Boolean = false) {
        val sameEndpoint = connectionKey == Pair(serverUrl, deviceId)
        connectionKey = Pair(serverUrl, deviceId)
        // Remote Config may change while paused; remember it without opening credentials or audio.
        if (!isVoiceEnabled) return
        if (!forceReconnect && sameEndpoint && webSocketClient != null) return
        val bearerToken = credentialStore.read(serverUrl, deviceId)
        val token = ++generation
        ++audioGeneration
        duplex.disconnected()
        audioRecorder.stopRecording()
        audioPlayer.stop()
        webSocketClient?.disconnect()
        webSocketClient = null
        if (bearerToken == null) {
            publish(JarvisState.ERROR, "Penyiapan perangkat diperlukan • tekan lama status untuk token pemilik")
            return
        }
        publish(JarvisState.DISCONNECTED, "Menghubungkan ke server...")
        // Fence callbacks from the old endpoint during Remote Config changes.
        val events = object : JarvisWebSocketClient.WebSocketEventListener {
            private fun dispatch(action: () -> Unit) { main.post { if (isVoiceEnabled && token == generation) action() } }
            override fun onConnected() = dispatch { this@JarvisKioskService.onConnected() }
            override fun onDisconnected() = dispatch { this@JarvisKioskService.onDisconnected() }
            override fun onStateChanged(state: JarvisState, message: String?, expression: String?) =
                dispatch { this@JarvisKioskService.onStateChanged(state, message, expression) }
            override fun onTelemetryReceived(amplitude: Float, bands: List<Float>?) =
                dispatch { this@JarvisKioskService.onTelemetryReceived(amplitude, bands) }
            override fun onTranscriptionReceived(role: String, text: String, isFinal: Boolean) =
                dispatch { this@JarvisKioskService.onTranscriptionReceived(role, text, isFinal) }
            override fun onAudioChunkReceived(audioData: ByteArray) =
                dispatch { this@JarvisKioskService.onAudioChunkReceived(audioData) }
            override fun onTTSStarted() = dispatch { this@JarvisKioskService.onTTSStarted() }
            override fun onTTSEnded() = dispatch { this@JarvisKioskService.onTTSEnded() }
        }
        webSocketClient = JarvisWebSocketClient(serverUrl, deviceId, bearerToken, events)
        webSocketClient?.connect()
    }

    private fun startForegroundNotification() {
        val channelId = "jarvis_kiosk_service"
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(channelId, "Jarvis Appliance Service", NotificationManager.IMPORTANCE_LOW)
            (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).createNotificationChannel(channel)
        }
        val status = when {
            !isVoiceEnabled -> "Mikrofon nonaktif • suara dijeda"
            ::audioRecorder.isInitialized && audioRecorder.isRecording() ->
                "Mikrofon aktif • lihat layar untuk status percakapan"
            else -> "Suara diaktifkan • mikrofon belum aktif"
        }
        val notification: Notification = NotificationCompat.Builder(this, channelId)
            .setContentTitle("Jarvis Appliance")
            .setContentText(status)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
        startForeground(1001, notification)
    }

    override fun onConnected() {
        if (!isVoiceEnabled) return
        // The client's onConnected is server_hello acknowledgement, not raw socket open.
        createAudioSession()
        duplex.connected()
        val started = audioRecorder.startRecording()
        duplex.microphoneReady(started)
        if (started) publishListening()
        else failAudioSession("Mikrofon gagal dimulai. Periksa izin dan perangkat audio.")
    }

    override fun onDisconnected() {
        if (!isVoiceEnabled) return
        ++audioGeneration
        duplex.disconnected()
        audioRecorder.stopRecording()
        audioPlayer.stop()
        publish(JarvisState.DISCONNECTED, "Koneksi terputus • mencoba menghubungkan ulang", "sedih")
    }

    override fun onStateChanged(state: JarvisState, message: String?, expression: String?) {
        if (!isVoiceEnabled) return
        duplex.stateChanged(state)
        if (duplex.playbackPending && (state == JarvisState.IDLE || state == JarvisState.LISTENING)) {
            publish(JarvisState.SPEAKING, "Menyelesaikan pemutaran • mikrofon dijeda", currentExpression)
        } else if (state == JarvisState.IDLE || state == JarvisState.LISTENING) {
            if (audioRecorder.isRecording()) publishListening()
            else publish(JarvisState.ERROR, "Mikrofon tidak aktif. Periksa izin dan perangkat audio.")
        } else {
            publish(state, message ?: when (state) {
                JarvisState.THINKING -> "Memproses • mikrofon dijeda"
                JarvisState.SPEAKING -> "Berbicara • mikrofon dijeda"
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
        audioPlayer.writeChunk(audioData)
    }

    override fun onTTSStarted() {
        if (!isVoiceEnabled) return
        duplex.ttsStarted()
        publish(JarvisState.SPEAKING, "Berbicara • mikrofon dijeda", currentExpression)
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
        voiceEnabled = false
        ++generation
        ++audioGeneration
        duplex.disconnected()
        serviceCallback = null
        super.onDestroy()
        audioRecorder.stopRecording()
        audioPlayer.release()
        webSocketClient?.disconnect()
        Log.i("JarvisKioskService", "JarvisKioskService destroyed.")
    }
}
