package id.my.yhdev.nexo.network

import android.util.Log
import com.google.gson.Gson
import com.google.gson.JsonObject
import id.my.yhdev.nexo.ui.NexoState
import okhttp3.*
import okio.ByteString
import okio.ByteString.Companion.toByteString
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import android.os.SystemClock
import id.my.yhdev.nexo.audio.core.UploadSession
import id.my.yhdev.nexo.audio.core.UtteranceBuffer

class NexoWebSocketClient(
    private val serverUrl: String,
    private val deviceId: String,
    private val bearerToken: String,
    private val listener: WebSocketEventListener
) {
    private val TAG = "NexoWebSocketClient"
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
    private val socketGeneration = AtomicLong()
    private val sendLock = Any()
    @Volatile var outdoorSession: OutdoorSession? = null
        private set
    @Volatile private var outputEpoch = 0L
    @Volatile private var outputAllowed = false
    private val uploadLifecycle = Any()
    @Volatile private var captureSendAllowed: () -> Boolean = { false }
    /** No uploader monitor is taken here. Actual sends share only the bounded send fence. */
    fun fenceCapture() { synchronized(sendLock) { captureSendAllowed = { false } } }
    private val uploadWorker = java.util.concurrent.ScheduledThreadPoolExecutor(1) { runnable ->
        Thread(runnable, "Nexo-UtteranceUpload").apply { isDaemon = true }
    }.apply {
        setExecuteExistingDelayedTasksAfterShutdownPolicy(false)
        setContinueExistingPeriodicTasksAfterShutdownPolicy(false)
    }
    val uploader = UploadSession(object : UploadSession.Transport {
        override fun control(value: MutableMap<String, Any>): Boolean = sendV2Map(value)
        override fun binary(value: ByteArray): Boolean = synchronized(sendLock) {
            if (outdoorSession == null || !protocolReady.get() || !captureSendAllowed()) false
            else webSocket?.send(value.toByteString()) == true
        }
        override fun queuedBytes(): Long = webSocket?.queueSize() ?: 131072
        override fun abort() { fenceOutput(); webSocket?.cancel() }
    })
    init {
        uploadWorker.scheduleAtFixedRate({
            runCatching {
                synchronized(uploadLifecycle) {
                    uploader.pump(SystemClock.elapsedRealtime())
                    if (uploader.state() == "DISCONNECTED" && protocolReady.get()) webSocket?.cancel()
                }
            }.onFailure { fenceOutput(); webSocket?.cancel() }
        }, 20, 20, TimeUnit.MILLISECONDS)
    }

    interface WebSocketEventListener {
        fun onConnected()
        fun onDisconnected()
        fun onStateChanged(state: NexoState, message: String?, expression: String?)
        fun onTelemetryReceived(amplitude: Float, bands: List<Float>?)
        fun onTranscriptionReceived(role: String, text: String, isFinal: Boolean)
        fun onAudioChunkReceived(audioData: ByteArray)
        fun onTTSStarted()
        fun onTTSEnded()
        fun onVoiceprintDialogReceived(dialog: VoiceprintDialog)
        fun onOutdoorControl(message: JsonObject) {}
        fun onOutdoorProtocolReady(session: OutdoorSession) {}
        fun usesTaggedOutput(): Boolean = false
        fun onOutputControl(message: JsonObject) {}
        fun onOutdoorAudio(connectionId: String, epoch: Long, audioData: ByteArray) {
            onAudioChunkReceived(audioData)
        }
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
            listener.onStateChanged(NexoState.ERROR, "URL server tidak valid.", null)
            return
        }
        val token = socketGeneration.incrementAndGet()
        val ended = AtomicBoolean(false)
        this.webSocket = client.newWebSocket(request, object : WebSocketListener() {
            private fun disconnected() {
                if (token != socketGeneration.get() || !ended.compareAndSet(false, true)) return
                synchronized(sendLock) { outdoorSession = null; fenceOutput() }
                uploader.disconnected()
                connecting.set(false)
                isConnected.set(false)
                protocolReady.set(false)
                acceptingPlayback.set(false)
                if (shouldRun.get()) listener.onDisconnected()
            }
            override fun onOpen(webSocket: WebSocket, response: Response) {
                Log.i(TAG, "WebSocket connected successfully.")
                if (!shouldRun.get() || token != socketGeneration.get()) { webSocket.cancel(); return }
                this@NexoWebSocketClient.webSocket = webSocket
                connecting.set(false)
                isConnected.set(true)
                // Socket open is not protocol ready: no PCM until server_hello.
                webSocket.send(gson.toJson(ClientHello(deviceId = deviceId)))
                Thread({
                    try {
                        Thread.sleep(10_000)
                        if (shouldRun.get() && !ended.get() && !protocolReady.get()) webSocket.cancel()
                    } catch (_: InterruptedException) {}
                }, "Nexo-Handshake-Timeout").apply { isDaemon = true; start() }
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                if (shouldRun.get() && !ended.get() && token == socketGeneration.get()) handleTextMessage(text, token)
            }

            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                if (shouldRun.get() && protocolReady.get() && outputAllowed && acceptingPlayback.get() &&
                    !ended.get() && token == socketGeneration.get()) {
                    val session = outdoorSession ?: return
                    listener.onOutdoorAudio(session.connectionId, outputEpoch, bytes.toByteArray())
                }
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                if (token != socketGeneration.get()) return
                Log.w(TAG, "WebSocket closing: $code")
                disconnected()
                if (code == 1008) setupRequired()
                webSocket.close(code, reason)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                if (token != socketGeneration.get()) return
                Log.w(TAG, "WebSocket closed: $code")
                disconnected()
                if (code == 1008) setupRequired()
                scheduleReconnect()
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                if (token != socketGeneration.get()) { response?.close(); return }
                Log.e(TAG, "WebSocket connection failed")
                disconnected()
                if (response?.code == 401 || response?.code == 403) setupRequired()
                response?.close()
                scheduleReconnect()
            }
        })
    }

    private fun handleTextMessage(jsonString: String) = handleTextMessage(jsonString, socketGeneration.get())

    private fun handleTextMessage(jsonString: String, token: Long) {
        if (!shouldRun.get() || token != socketGeneration.get()) return
        try {
            require(jsonString.toByteArray(Charsets.UTF_8).size <= 4096)
            val json = gson.fromJson(jsonString, JsonObject::class.java)
            val type = OutdoorProtocol.string(json, "type") ?: error("invalid_message")
            val session = outdoorSession
            if (type != "server_hello" && type != "ping" && type != "pong") {
                if (session == null || OutdoorProtocol.uuid(json, "connection_id") != session.connectionId) return
                if (type in setOf("state_change", "cognitive", "transcription", "tts_start", "tts_end", "visual_telemetry")) {
                    val epoch = OutdoorProtocol.integer(json, "output_epoch", 0, Long.MAX_VALUE) ?: return
                    if (!outputAllowed || epoch != outputEpoch) return
                }
            }
            if (listener.usesTaggedOutput() && type in setOf("state_change", "cognitive", "transcription", "tts_start", "tts_end", "visual_telemetry")) {
                if (type == "tts_start") {
                    val valid = OutdoorProtocol.string(json, "format") == "audio/pcm" &&
                        OutdoorProtocol.integer(json, "sample_rate", 24000, 24000) == 24000L &&
                        OutdoorProtocol.integer(json, "channels", 1, 1) == 1L &&
                        OutdoorProtocol.integer(json, "bit_depth", 16, 16) == 16L
                    if (!valid) error("invalid_format")
                    acceptingPlayback.set(true)
                }
                if (type == "tts_end" && !acceptingPlayback.getAndSet(false)) return
                listener.onOutputControl(json)
                return
            }
            when (type) {
                "server_hello" -> {
                    if (protocolReady.get()) error("duplicate_hello")
                    val selected = OutdoorProtocol.negotiate(json)
                    if (selected == null) {
                        shouldRun.set(false)
                        fenceOutput()
                        listener.onStateChanged(NexoState.ERROR, "Server perlu pembaruan audio v2. Mikrofon tetap OFF.", null)
                        webSocket?.cancel()
                        return
                    }
                    outdoorSession = selected
                    outputEpoch = selected.outputEpoch
                    fenceOutput()
                    synchronized(uploadLifecycle) {
                        protocolReady.set(true)
                        uploader.connect(selected.connectionId, selected.serverInstanceId, selected.maxChunkBytes, true, SystemClock.elapsedRealtime())
                    }
                    reconnectAttempt = 0
                    val generation = socketGeneration.get()
                    listener.onOutdoorProtocolReady(selected)
                    if (shouldRun.get() && protocolReady.get() && generation == socketGeneration.get()) listener.onConnected()
                }
                "utterance_ack" -> {
                    val id = OutdoorProtocol.uuid(json, "utterance_id") ?: error("invalid_message")
                    val request = OutdoorProtocol.uuid(json, "request_id") ?: error("invalid_message")
                    val stage = OutdoorProtocol.string(json, "stage") ?: error("invalid_message")
                    val next = if (json.has("next_sample")) OutdoorProtocol.integer(json, "next_sample", 0, 320000)?.toInt()
                        ?: error("invalid_length") else 0
                    val samples = if (json.has("total_samples")) OutdoorProtocol.integer(json, "total_samples", 1, 320000)?.toInt()
                        ?: error("invalid_length") else 0
                    uploader.ack(session!!.connectionId, request, id, stage, next, samples,
                        OutdoorProtocol.string(json, "sha256"), SystemClock.elapsedRealtime())
                    listener.onOutdoorControl(json)
                }
                "utterance_error" -> {
                    uploader.serverError(OutdoorProtocol.uuid(json, "utterance_id") ?: error("invalid_message"),
                        OutdoorProtocol.string(json, "code") ?: "invalid_message")
                    listener.onOutdoorControl(json)
                }
                "utterance_result" -> {
                    uploader.result(OutdoorProtocol.uuid(json, "utterance_id") ?: error("invalid_message"))
                    listener.onOutdoorControl(json)
                }
                "audio_owner_ack", "conversation_resumed", "enrollment_ack" -> listener.onOutdoorControl(json)
                "state_change" -> {
                    val stateStr = json.get("state")?.takeUnless { it.isJsonNull }?.asString ?: "ERROR"
                    val msg = json.get("message")?.takeUnless { it.isJsonNull }?.asString
                    val expr = json.get("expression")?.takeUnless { it.isJsonNull }?.asString
                    val state = runCatching { NexoState.valueOf(stateStr) }.getOrDefault(NexoState.ERROR)
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
                        listener.onStateChanged(NexoState.ERROR, "Format audio server tidak didukung (perlu PCM16 24 kHz mono).", null)
                        webSocket?.cancel()
                    }
                }
                "tts_end" -> if (acceptingPlayback.getAndSet(false)) listener.onTTSEnded()
                "ping" -> {
                    val ts = json.get("timestamp")?.asLong ?: System.currentTimeMillis()
                    sendJson(PongMessage(type = "pong", timestamp = ts))
                }
                "pong" -> Unit
                "cognitive" -> listener.onOutdoorControl(json)
                "voiceprint_dialog" -> {
                    val dialog = runCatching { gson.fromJson(jsonString, VoiceprintDialog::class.java) }.getOrNull()
                    if (dialog != null) {
                        listener.onVoiceprintDialogReceived(dialog)
                    }
                }
                else -> error("invalid_message")
            }
        } catch (e: Exception) {
            Log.e(TAG, "Invalid server control frame")
            fenceOutput()
            protocolReady.set(false)
            webSocket?.close(1008, "invalid_message")
            webSocket?.cancel()
        }
    }

    fun queryVoiceprint() {
        sendJson(VoiceprintQuery())
    }

    fun unenrollVoiceprint(requestId: String = java.util.UUID.randomUUID().toString()): Boolean =
        sendJson(VoiceprintControl("voiceprint_unenroll", requestId))

    @Deprecated("Use enrollment_begin under an acknowledged exclusive lease")
    fun startVoiceprintEnrollment(requestId: String): Boolean = false

    @Deprecated("Use enrollment_save under an acknowledged exclusive lease")
    fun finishVoiceprintEnrollment(requestId: String): Boolean = false

    @Deprecated("Use enrollment_cancel under an acknowledged exclusive lease")
    fun cancelVoiceprintEnrollment(requestId: String): Boolean = false

    fun sendJson(obj: Any): Boolean {
        if (!isConnected.get() || !shouldRun.get()) return false
        val json = gson.toJsonTree(obj).asJsonObject
        val type = OutdoorProtocol.string(json, "type") ?: return false
        if (type !in setOf("pong", "voiceprint_query", "voiceprint_unenroll")) return false
        val session = outdoorSession ?: return false
        json.addProperty("connection_id", session.connectionId)
        if (!json.has("request_id")) json.addProperty("request_id", java.util.UUID.randomUUID().toString())
        val request = OutdoorProtocol.uuid(json, "request_id") ?: return false
        val fields = json.entrySet().associate { it.key to (it.value as Any) }.toMutableMap()
        fields["request_id"] = request
        fields["connection_id"] = session.connectionId
        return sendV2Map(fields)
    }

    @Deprecated("Raw PCM is forbidden in v2; use uploadUtterance after local seal")
    fun sendAudioChunk(chunk: ByteArray, length: Int): Boolean = false

    fun uploadUtterance(pcm: UtteranceBuffer.Sealed, leaseId: String? = null, enrollmentId: String? = null,
        stillCurrent: () -> Boolean = { true }): Boolean {
        val result = java.util.concurrent.CompletableFuture<Boolean>()
        runCatching { uploadWorker.execute {
            if (!shouldRun.get() || !stillCurrent()) { result.complete(false); return@execute }
            synchronized(sendLock) { captureSendAllowed = stillCurrent }
            result.complete(runCatching {
                uploader.begin(pcm, leaseId, enrollmentId, SystemClock.elapsedRealtime())
            }.getOrDefault(false))
        } }.onFailure { result.complete(false) }
        return runCatching { result.get(5, TimeUnit.SECONDS) }.getOrDefault(false)
    }

    fun cancelUtterance() {
        fenceCapture()
        runCatching { uploadWorker.execute { uploader.cancel(SystemClock.elapsedRealtime()) } }
    }

    fun fenceOutput() { outputAllowed = false; acceptingPlayback.set(false) }

    /** Service calls only after a matched explicit resume ACK and its owner/generation checks. */
    fun allowOutput(connectionId: String, epoch: Long): Boolean {
        val session = outdoorSession ?: return false
        if (session.connectionId != connectionId || epoch < outputEpoch) return false
        outputEpoch = epoch
        outputAllowed = true
        return true
    }

    fun sendOutdoorControl(type: String, requestId: String, fields: Map<String, Any> = emptyMap()): Boolean {
        if (type !in setOf("audio_owner_acquire", "audio_owner_renew", "audio_owner_release", "conversation_resume",
                "enrollment_begin", "enrollment_save", "enrollment_cancel")) return false
        runCatching { UploadSession.uuid(requestId) }.getOrElse { return false }
        val connection = outdoorSession?.connectionId ?: return false
        val value = fields.toMutableMap()
        if (value.keys.any { it in setOf("type", "connection_id", "request_id") }) return false
        value["type"] = type; value["request_id"] = requestId; value["connection_id"] = connection
        return sendV2Map(value)
    }

    private fun sendV2Map(value: Map<String, Any>): Boolean = synchronized(sendLock) {
        val session = outdoorSession
        val captureControl = value["type"] in setOf("utterance_begin", "utterance_commit")
        if ((captureControl && !captureSendAllowed()) || session == null || value["connection_id"] != session.connectionId || !protocolReady.get() || !shouldRun.get()) false
        else {
            val text = gson.toJson(value)
            val bytes = text.toByteArray(Charsets.UTF_8).size
            val socket = webSocket
            bytes <= 4096 && socket != null && socket.queueSize() + bytes <= 131072 && socket.send(text)
        }
    }

    private fun setupRequired() {
        shouldRun.set(false)
        protocolReady.set(false)
        acceptingPlayback.set(false)
        listener.onStateChanged(NexoState.ERROR,
            "Penyiapan perangkat diperlukan • tekan lama status untuk token pemilik", null)
    }

    private fun scheduleReconnect() {
        if (!shouldRun.get() || !reconnectPending.compareAndSet(false, true)) return
        reconnectAttempt++
        val delaySec = minOf(30, (1 shl minOf(reconnectAttempt, 5)))
        Log.i(TAG, "Scheduling reconnect in $delaySec seconds...")
        val expr = if (reconnectAttempt <= 2) "bingung" else "sedih"
        Thread({
            try {
                for (remaining in delaySec downTo 1) {
                    if (!shouldRun.get() || isConnected.get()) break
                    val msg = if (reconnectAttempt <= 2) {
                        "Koneksi terputus • mencoba ulang dalam ${remaining}s (ke-$reconnectAttempt)"
                    } else {
                        "Server belum merespons • mencoba ulang dalam ${remaining}s (ke-$reconnectAttempt)"
                    }
                    listener.onStateChanged(NexoState.DISCONNECTED, msg, expr)
                    Thread.sleep(1000L)
                }
                reconnectPending.set(false)
                if (shouldRun.get() && !isConnected.get()) {
                    listener.onStateChanged(
                        NexoState.DISCONNECTED,
                        "Menghubungkan ke server (percobaan $reconnectAttempt)...",
                        "bingung"
                    )
                    connect()
                }
            } catch (ignored: InterruptedException) {}
        }, "Nexo-Reconnect-Thread").start()
    }

    fun disconnect() {
        socketGeneration.incrementAndGet()
        fenceOutput()
        fenceCapture()
        shouldRun.set(false)
        runCatching { uploadWorker.execute {
            uploader.cancel(SystemClock.elapsedRealtime())
            uploader.clear()
        } }
        uploadWorker.shutdown()
        outdoorSession = null
        isConnected.set(false)
        protocolReady.set(false)
        webSocket?.cancel()
        webSocket?.close(1000, "App closing")
        client.dispatcher.executorService.shutdown()
    }
}
