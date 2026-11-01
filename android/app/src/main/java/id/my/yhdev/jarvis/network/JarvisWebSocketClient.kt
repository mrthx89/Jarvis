package id.my.yhdev.jarvis.network

import android.util.Log
import com.google.gson.Gson
import com.google.gson.JsonObject
import id.my.yhdev.jarvis.ui.JarvisState
import okhttp3.*
import okio.ByteString
import okio.ByteString.Companion.toByteString
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

class JarvisWebSocketClient(
    private val serverUrl: String,
    private val deviceId: String,
    private val bearerToken: String,
    private val listener: WebSocketEventListener
) {
    private val TAG = "JarvisWebSocketClient"
    private val gson = Gson()
    private val client = OkHttpClient.Builder()
        .followRedirects(false)
        .followSslRedirects(false)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .pingInterval(15, TimeUnit.SECONDS)
        .build()

    @Volatile private var webSocket: WebSocket? = null
    private val isConnected = AtomicBoolean(false)
    private val shouldRun = AtomicBoolean(true)
    private val protocolReady = AtomicBoolean(false)
    private val acceptingPlayback = AtomicBoolean(false)
    private val connecting = AtomicBoolean(false)
    private val reconnectPending = AtomicBoolean(false)
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
        if (!shouldRun.get() || isConnected.get() || !connecting.compareAndSet(false, true)) return
        Log.i(TAG, "Connecting (attempt: $reconnectAttempt)")

        val request = try {
            Request.Builder().url(serverUrl)
                .header("Authorization", "Bearer $bearerToken")
                .header("X-Device-ID", deviceId).build()
        } catch (e: IllegalArgumentException) {
            connecting.set(false)
            listener.onStateChanged(JarvisState.ERROR, "URL server tidak valid.", null)
            return
        }
        val ended = AtomicBoolean(false)
        this.webSocket = client.newWebSocket(request, object : WebSocketListener() {
            private fun disconnected() {
                if (!ended.compareAndSet(false, true)) return
                connecting.set(false)
                isConnected.set(false)
                protocolReady.set(false)
                acceptingPlayback.set(false)
                if (shouldRun.get()) listener.onDisconnected()
            }
            override fun onOpen(webSocket: WebSocket, response: Response) {
                Log.i(TAG, "WebSocket connected successfully.")
                if (!shouldRun.get()) { webSocket.cancel(); return }
                this@JarvisWebSocketClient.webSocket = webSocket
                connecting.set(false)
                isConnected.set(true)
                // Socket open is not protocol ready: no PCM until server_hello.
                webSocket.send(gson.toJson(ClientHello(deviceId = deviceId)))
                Thread({
                    try {
                        Thread.sleep(10_000)
                        if (shouldRun.get() && !ended.get() && !protocolReady.get()) webSocket.cancel()
                    } catch (_: InterruptedException) {}
                }, "Jarvis-Handshake-Timeout").apply { isDaemon = true; start() }
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                if (shouldRun.get() && !ended.get()) handleTextMessage(text)
            }

            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                if (shouldRun.get() && protocolReady.get() && acceptingPlayback.get() && !ended.get()) {
                    listener.onAudioChunkReceived(bytes.toByteArray())
                }
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                Log.w(TAG, "WebSocket closing: $code")
                disconnected()
                if (code == 1008) setupRequired()
                webSocket.close(code, reason)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                Log.w(TAG, "WebSocket closed: $code")
                disconnected()
                if (code == 1008) setupRequired()
                scheduleReconnect()
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                Log.e(TAG, "WebSocket connection failed")
                disconnected()
                if (response?.code == 401 || response?.code == 403) setupRequired()
                response?.close()
                scheduleReconnect()
            }
        })
    }

    private fun handleTextMessage(jsonString: String) {
        try {
            val json = gson.fromJson(jsonString, JsonObject::class.java)
            when (json.get("type")?.takeUnless { it.isJsonNull }?.asString) {
                "server_hello" -> {
                    if (isConnected.get() && protocolReady.compareAndSet(false, true)) {
                        reconnectAttempt = 0
                        listener.onConnected()
                    }
                }
                "state_change" -> {
                    val stateStr = json.get("state")?.takeUnless { it.isJsonNull }?.asString ?: "ERROR"
                    val msg = json.get("message")?.takeUnless { it.isJsonNull }?.asString
                    val expr = json.get("expression")?.takeUnless { it.isJsonNull }?.asString
                    val state = runCatching { JarvisState.valueOf(stateStr) }.getOrDefault(JarvisState.ERROR)
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
                "tts_start" -> {
                    val valid = json.get("format")?.takeUnless { it.isJsonNull }?.asString == "audio/pcm" &&
                        json.get("sample_rate")?.takeUnless { it.isJsonNull }?.asInt == 24000 &&
                        json.get("channels")?.takeUnless { it.isJsonNull }?.asInt == 1 &&
                        (json.get("bit_depth") == null || json.get("bit_depth")?.takeUnless { it.isJsonNull }?.asInt == 16)
                    acceptingPlayback.set(valid)
                    if (valid) listener.onTTSStarted()
                    else {
                        listener.onStateChanged(JarvisState.ERROR, "Format audio server tidak didukung (perlu PCM16 24 kHz mono).", null)
                        webSocket?.cancel()
                    }
                }
                "tts_end" -> if (acceptingPlayback.getAndSet(false)) listener.onTTSEnded()
                "ping" -> {
                    val ts = json.get("timestamp")?.asLong ?: System.currentTimeMillis()
                    sendJson(PongMessage(type = "pong", timestamp = ts))
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "Invalid server control frame")
        }
    }

    fun sendJson(obj: Any) {
        if (!isConnected.get()) return
        val json = gson.toJson(obj)
        webSocket?.send(json)
    }

    fun sendAudioChunk(chunk: ByteArray, length: Int): Boolean {
        if (!isConnected.get() || !protocolReady.get() || !shouldRun.get()) return false
        val sent = webSocket?.send(chunk.toByteString(0, length)) == true
        if (!sent) {
            protocolReady.set(false)
            webSocket?.cancel()
        }
        return sent
    }

    private fun setupRequired() {
        shouldRun.set(false)
        protocolReady.set(false)
        acceptingPlayback.set(false)
        listener.onStateChanged(JarvisState.ERROR,
            "Penyiapan perangkat diperlukan • tekan lama status untuk token pemilik", null)
    }

    private fun scheduleReconnect() {
        if (!shouldRun.get() || !reconnectPending.compareAndSet(false, true)) return
        reconnectAttempt++
        val delaySec = minOf(30, (1 shl minOf(reconnectAttempt, 5)))
        Log.i(TAG, "Scheduling reconnect in $delaySec seconds...")
        Thread({
            try {
                Thread.sleep(delaySec * 1000L)
                reconnectPending.set(false)
                if (shouldRun.get() && !isConnected.get()) {
                    connect()
                }
            } catch (ignored: InterruptedException) {}
        }, "Jarvis-Reconnect-Thread").start()
    }

    fun disconnect() {
        shouldRun.set(false)
        isConnected.set(false)
        protocolReady.set(false)
        webSocket?.cancel()
        webSocket?.close(1000, "App closing")
        client.dispatcher.executorService.shutdown()
    }
}
