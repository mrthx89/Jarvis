package com.yhdev.jarvis.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Binder
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import com.yhdev.jarvis.audio.AudioPlayerManager
import com.yhdev.jarvis.audio.AudioRecorderManager
import com.yhdev.jarvis.network.JarvisWebSocketClient
import com.yhdev.jarvis.ui.JarvisState

class JarvisKioskService : Service(), JarvisWebSocketClient.WebSocketEventListener {

    private val TAG = "JarvisKioskService"
    private val binder = LocalBinder()

    private lateinit var audioRecorder: AudioRecorderManager
    private lateinit var audioPlayer: AudioPlayerManager
    private var webSocketClient: JarvisWebSocketClient? = null

    var serviceCallback: ServiceCallback? = null

    interface ServiceCallback {
        fun onStateChanged(state: JarvisState, message: String?, expression: String?)
        fun onTelemetryReceived(amplitude: Float, bands: List<Float>?)
        fun onTranscriptionReceived(role: String, text: String, isFinal: Boolean)
    }

    inner class LocalBinder : Binder() {
        fun getService(): JarvisKioskService = this@JarvisKioskService
    }

    override fun onBind(intent: Intent?): IBinder = binder

    override fun onCreate() {
        super.onCreate()
        startForegroundNotification()

        audioPlayer = AudioPlayerManager()

        audioRecorder = AudioRecorderManager { chunk, length ->
            webSocketClient?.sendAudioChunk(chunk, length)
        }
    }

    fun initializeConnection(serverUrl: String, deviceId: String) {
        webSocketClient?.disconnect()
        webSocketClient = JarvisWebSocketClient(serverUrl, deviceId, this)
        webSocketClient?.connect()
    }

    private fun startForegroundNotification() {
        val channelId = "jarvis_kiosk_service"
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                channelId,
                "Jarvis Appliance Service",
                NotificationManager.IMPORTANCE_LOW
            )
            val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            manager.createNotificationChannel(channel)
        }

        val notification: Notification = NotificationCompat.Builder(this, channelId)
            .setContentTitle("Jarvis Appliance Active")
            .setContentText("Monitoring hands-free audio and ambient visualizer")
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()

        startForeground(1001, notification)
    }

    override fun onConnected() {
        audioRecorder.startRecording()
        serviceCallback?.onStateChanged(JarvisState.IDLE, null, "netral")
    }

    override fun onDisconnected() {
        serviceCallback?.onStateChanged(JarvisState.DISCONNECTED, null, "sedih")
    }

    override fun onStateChanged(state: JarvisState, message: String?, expression: String?) {
        serviceCallback?.onStateChanged(state, message, expression)
    }

    override fun onTelemetryReceived(amplitude: Float, bands: List<Float>?) {
        serviceCallback?.onTelemetryReceived(amplitude, bands)
    }

    override fun onTranscriptionReceived(role: String, text: String, isFinal: Boolean) {
        serviceCallback?.onTranscriptionReceived(role, text, isFinal)
    }

    override fun onAudioChunkReceived(audioData: ByteArray) {
        audioPlayer.writeChunk(audioData)
    }

    override fun onTTSStarted() {
        audioPlayer.start()
    }

    override fun onTTSEnded() {
        // Natural transition back to listening/idle handled via state_change
    }

    override fun onDestroy() {
        super.onDestroy()
        audioRecorder.stopRecording()
        audioPlayer.stop()
        webSocketClient?.disconnect()
        Log.i(TAG, "JarvisKioskService destroyed.")
    }
}
