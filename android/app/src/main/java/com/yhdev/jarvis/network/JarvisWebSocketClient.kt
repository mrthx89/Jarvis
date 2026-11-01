package com.yhdev.jarvis.network

import android.util.Log
import com.google.gson.Gson
import com.google.gson.JsonObject
import com.yhdev.jarvis.ui.JarvisState
import okhttp3.*
import okio.ByteString
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

class JarvisWebSocketClient(
    private val serverUrl: String,
    private val deviceId: String,
    private val listener: WebSocketEventListener
) {
    private val TAG = "JarvisWebSocketClient"
    private val gson = Gson()
    private val client = OkHttpClient.Builder()
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .pingInterval(15, TimeUnit.SECONDS)
        .build()

    private var webSocket: WebSocket? = null
    private val isConnected = AtomicBoolean(false)
    private val shouldRun = AtomicBoolean(true)
    private var reconnectAttempt = 0

    interface WebSocketEventListener {
        fun onConnected()
        fun onDisconnected()
        fun onStateChanged(state: JarvisState, message: String?, expression: String?)
        fun onTelemetryReceived(amplitude: Float, bands: List<Float>?)
        fun onTranscriptionReceived(role: String, text: String, isFinal: Boolean)
        fun onAudioChunkReceived(audioData: ByteArray)
        fun onTTSStarted()
        fun onTTSEnded()
    }

    fun connect() {
        if (!shouldRun.get()) return
        Log.i(TAG, "Connecting to $serverUrl (attempt: $reconnectAttempt)...")

        val request = Request.Builder().url(serverUrl).build()
        webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(ws: WebSocket, response: Response) {
                Log.i(TAG, "WebSocket connected successfully.")
                isConnected.set(true)
                reconnectAttempt = 0
                listener.onConnected()

                // Send ClientHello handshake
                val hello = ClientHello(deviceId = deviceId)
                sendJson(hello)
            }

            override fun onMessage(ws: WebSocket, text: String) {
                handleTextMessage(text)
            }

            override fun onMessage(ws: WebSocket, bytes: ByteString) {
                listener.onAudioChunkReceived(bytes.toByteArray())
            }

            override fun onClosing(ws: WebSocket, code: Int, reason: String) {
                Log.w(TAG, "WebSocket closing: $code / $reason")
                isConnected.set(false)
            }

            override fun onClosed(ws: WebSocket, code: Int, reason: String) {
                Log.w(TAG, "WebSocket closed: $code / $reason")
                isConnected.set(false)
                listener.onDisconnected()
                scheduleReconnect()
            }

            override fun onFailure(ws: WebSocket, t: Throwable, response: Response?) {
                Log.e(TAG, "WebSocket failure: ${t.message}")
                isConnected.set(false)
                listener.onDisconnected()
                scheduleReconnect()
            }
        })
    }

    private fun handleTextMessage(jsonString: String) {
        try {
            val json = gson.fromJson(jsonString, JsonObject::class.java)
            when (json.get("type")?.asString) {
                "state_change" -> {
                    val stateStr = json.get("state")?.asString ?: "IDLE"
                    val msg = json.get("message")?.asString
                    val expr = json.get("expression")?.asString
                    val state = runCatching { JarvisState.valueOf(stateStr) }.getOrDefault(JarvisState.IDLE)
                    listener.onStateChanged(state, msg, expr)
                }
                "visual_telemetry" -> {
                    val amp = json.get("amplitude")?.asFloat ?: 0f
                    listener.onTelemetryReceived(amp, null)
                }
                "transcription" -> {
                    val role = json.get("role")?.asString ?: "assistant"
                    val text = json.get("text")?.asString ?: ""
                    val isFinal = json.get("is_final")?.asBoolean ?: true
                    listener.onTranscriptionReceived(role, text, isFinal)
                }
                "tts_start" -> listener.onTTSStarted()
                "tts_end" -> listener.onTTSEnded()
                "ping" -> {
                    val ts = json.get("timestamp")?.asLong ?: System.currentTimeMillis()
                    sendJson(PongMessage(type = "pong", timestamp = ts))
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "Error parsing incoming text frame: ${e.message}")
        }
    }

    fun sendJson(obj: Any) {
        if (!isConnected.get()) return
        val json = gson.toJson(obj)
        webSocket?.send(json)
    }

    fun sendAudioChunk(chunk: ByteArray, length: Int) {
        if (!isConnected.get()) return
        val byteString = ByteString.of(chunk, 0, length)
        webSocket?.send(byteString)
    }

    private fun scheduleReconnect() {
        if (!shouldRun.get()) return
        reconnectAttempt++
        val delaySec = minOf(30, (1 shl minOf(reconnectAttempt, 5)))
        Log.i(TAG, "Scheduling reconnect in $delaySec seconds...")
        Thread({
            try {
                Thread.sleep(delaySec * 1000L)
                if (shouldRun.get() && !isConnected.get()) {
                    connect()
                }
            } catch (ignored: InterruptedException) {}
        }, "Jarvis-Reconnect-Thread").start()
    }

    fun disconnect() {
        shouldRun.set(false)
        isConnected.set(false)
        webSocket?.close(1000, "App closing")
        client.dispatcher.executorService.shutdown()
    }
}
