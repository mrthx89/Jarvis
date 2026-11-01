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

class JarvisKioskService : Service(), JarvisWebSocketClient.WebSocketEventListener {
    private val binder = LocalBinder()
    private val main = Handler(Looper.getMainLooper())
    private val duplex = HalfDuplexGate()
    private lateinit var audioRecorder: AudioRecorderManager
    private lateinit var audioPlayer: AudioPlayerManager
    @Volatile private var webSocketClient: JarvisWebSocketClient? = null
    @Volatile private var generation = 0
    private var connectionKey: Pair<String, String>? = null
    private var currentState = JarvisState.DISCONNECTED
    private var currentMessage: String? = "Belum terhubung ke server."
    private var currentExpression: String? = null

    var serviceCallback: ServiceCallback? = null
        set(value) {
            field = value
            value?.onStateChanged(currentState, currentMessage, currentExpression)
        }

    interface ServiceCallback {
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
    }

    private fun publishListening() {
        if (duplex.canSendAudio) {
            publish(JarvisState.LISTENING, "Mikrofon aktif • silakan bicara", "netral")
        }
    }

    override fun onCreate() {
        super.onCreate()
        startForegroundNotification()
        audioPlayer = AudioPlayerManager(onError = { message ->
            val token = generation
            main.post {
                if (token != generation) return@post
                // Output failure is terminal for this connection, not a successful drain.
                duplex.playbackFailed()
                val reconnect = connectionKey
                connectionKey = null // bypass same-endpoint initialization guard
                if (reconnect != null) {
                    initializeConnection(reconnect.first, reconnect.second)
                } else {
                    audioRecorder.stopRecording()
                    audioPlayer.stop()
                }
                publish(JarvisState.ERROR, "$message • menghubungkan ulang")
            }
        })
        audioRecorder = AudioRecorderManager(onError = { message ->
            main.post {
                duplex.microphoneReady(false)
                audioRecorder.stopRecording()
                publish(JarvisState.ERROR, message)
            }
        }) { chunk, length ->
            // Continuous PCM for server endpointing. No local voice_end, no barge-in.
            if (duplex.canSendAudio && webSocketClient?.sendAudioChunk(chunk, length) != true) {
                val token = generation
                duplex.disconnected()
                main.post { if (token == generation) onDisconnected() }
            }
        }
    }

    fun initializeConnection(serverUrl: String, deviceId: String) {
        if (connectionKey == Pair(serverUrl, deviceId) && webSocketClient != null) return
        connectionKey = Pair(serverUrl, deviceId)
        val token = ++generation
        duplex.disconnected()
        audioRecorder.stopRecording()
        audioPlayer.stop()
        webSocketClient?.disconnect()
        publish(JarvisState.DISCONNECTED, "Menghubungkan ke server...")
        // Fence callbacks from the old endpoint during Remote Config changes.
        val events = object : JarvisWebSocketClient.WebSocketEventListener {
            private fun dispatch(action: () -> Unit) { main.post { if (token == generation) action() } }
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
        webSocketClient = JarvisWebSocketClient(serverUrl, deviceId, events)
        webSocketClient?.connect()
    }

    private fun startForegroundNotification() {
        val channelId = "jarvis_kiosk_service"
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(channelId, "Jarvis Appliance Service", NotificationManager.IMPORTANCE_LOW)
            (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).createNotificationChannel(channel)
        }
        val notification: Notification = NotificationCompat.Builder(this, channelId)
            .setContentTitle("Jarvis Appliance Active")
            .setContentText("Layanan suara aktif • lihat layar untuk status koneksi")
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
        startForeground(1001, notification)
    }

    override fun onConnected() {
        duplex.connected()
        val started = audioRecorder.startRecording()
        duplex.microphoneReady(started)
        if (started) publishListening()
        else publish(JarvisState.ERROR, "Mikrofon gagal dimulai. Periksa izin dan perangkat audio.")
    }

    override fun onDisconnected() {
        duplex.disconnected()
        audioRecorder.stopRecording()
        audioPlayer.stop()
        publish(JarvisState.DISCONNECTED, "Koneksi terputus • mencoba menghubungkan ulang", "sedih")
    }

    override fun onStateChanged(state: JarvisState, message: String?, expression: String?) {
        duplex.stateChanged(state)
        if (duplex.playbackPending && (state == JarvisState.IDLE || state == JarvisState.LISTENING)) {
            publish(JarvisState.SPEAKING, "Menyelesaikan pemutaran • mikrofon dijeda", expression)
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
        serviceCallback?.onTelemetryReceived(amplitude, bands)
    }

    override fun onTranscriptionReceived(role: String, text: String, isFinal: Boolean) {
        serviceCallback?.onTranscriptionReceived(role, text, isFinal)
    }

    override fun onAudioChunkReceived(audioData: ByteArray) {
        duplex.ttsStarted()
        audioPlayer.writeChunk(audioData)
    }

    override fun onTTSStarted() {
        duplex.ttsStarted()
        publish(JarvisState.SPEAKING, "Berbicara • mikrofon dijeda")
        audioPlayer.start()
    }

    override fun onTTSEnded() {
        val token = generation
        audioPlayer.finish {
            main.post {
                if (token == generation) {
                    duplex.playbackDrained()
                    publishListening()
                }
            }
        }
    }

    override fun onDestroy() {
        ++generation
        duplex.disconnected()
        serviceCallback = null
        super.onDestroy()
        audioRecorder.stopRecording()
        audioPlayer.release()
        webSocketClient?.disconnect()
        Log.i("JarvisKioskService", "JarvisKioskService destroyed.")
    }
}
