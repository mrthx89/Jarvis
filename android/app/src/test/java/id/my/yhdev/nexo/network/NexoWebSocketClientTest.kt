package id.my.yhdev.nexo.network

import id.my.yhdev.nexo.ui.NexoState
import org.junit.Assert.*
import org.junit.Test
import okhttp3.*
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import okio.ByteString

class NexoWebSocketClientTest {
    private class Events : NexoWebSocketClient.WebSocketEventListener {
        var state: NexoState? = null
        var message: String? = "not delivered"
        var expression: String? = "not delivered"
        val connected = CountDownLatch(1)
        val setup = CountDownLatch(1)
        override fun onConnected() { connected.countDown() }
        override fun onDisconnected() {}
        override fun onStateChanged(state: NexoState, message: String?, expression: String?) {
            this.state = state
            this.message = message
            this.expression = expression
            if (message?.contains("Penyiapan perangkat diperlukan") == true) setup.countDown()
        }
        override fun onTelemetryReceived(amplitude: Float, bands: List<Float>?) {}
        override fun onTranscriptionReceived(role: String, text: String, isFinal: Boolean) {}
        override fun onAudioChunkReceived(audioData: ByteArray) {}
        var ttsStarts = 0
        override fun onTTSStarted() { ttsStarts++ }
        override fun onTTSEnded() {}
        override fun onVoiceprintDialogReceived(dialog: VoiceprintDialog) {}
        var onReady: (() -> Unit)? = null
        override fun onOutdoorProtocolReady(session: OutdoorSession) { onReady?.invoke() }
    }

    private val helloV2 = """{"type":"server_hello","protocol_version":"2.0","capabilities":["utterance_upload_v2","exclusive_audio_v1"],"connection_id":"00000000-0000-4000-8000-000000000010","server_instance_id":"00000000-0000-4000-8000-000000000020","conversation_paused":true,"output_epoch":0,"limits":{"max_samples":320000,"max_chunk_bytes":16384,"retry_ttl_ms":30000,"owner_lease_ms":15000}}"""
    private val envelope = "\"connection_id\":\"00000000-0000-4000-8000-000000000010\",\"output_epoch\":0"
    private fun parse(client: NexoWebSocketClient, value: String) {
        val method = client.javaClass.getDeclaredMethod("handleTextMessage", String::class.java)
        method.isAccessible = true
        method.invoke(client, value)
    }
    private fun ready(client: NexoWebSocketClient) {
        parse(client, helloV2)
        assertTrue(client.allowOutput("00000000-0000-4000-8000-000000000010", 0))
    }

    @Test fun teardownDuringReadyNeverPublishesConnected() {
        val events = Events()
        val client = NexoWebSocketClient("ws://localhost/", "test", "a".repeat(43), events)
        events.onReady = { client.disconnect() }
        parse(client, helloV2)
        assertEquals(1L, events.connected.count)
        assertNull(client.outdoorSession)
    }

    @Test fun legacyEnrollmentCannotSendOnV2() {
        val events = Events()
        val client = NexoWebSocketClient("ws://localhost/", "test", "a".repeat(43), events)
        try {
            ready(client)
            assertFalse(client.startVoiceprintEnrollment("test"))
            assertFalse(client.finishVoiceprintEnrollment("test"))
            assertFalse(client.cancelVoiceprintEnrollment("test"))
        } finally { client.disconnect() }
    }

    @Test fun waitsForServerHelloBeforeConnectedOrSendingPcm() {
        val server = MockWebServer()
        val hello = LinkedBlockingQueue<String>()
        val audio = LinkedBlockingQueue<ByteString>()
        lateinit var peer: WebSocket
        server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) { peer = webSocket }
            override fun onMessage(webSocket: WebSocket, text: String) { hello.offer(text) }
            override fun onMessage(webSocket: WebSocket, bytes: ByteString) { audio.offer(bytes) }
        }))
        server.start()
        val events = Events()
        val client = NexoWebSocketClient(server.url("/").toString(), "test", "a".repeat(43), events)
        try {
            client.connect()
            val request = server.takeRequest(3, TimeUnit.SECONDS)!!
            assertEquals("Bearer " + "a".repeat(43), request.getHeader("Authorization"))
            assertEquals("test", request.getHeader("X-Device-ID"))
            assertTrue(hello.poll(3, TimeUnit.SECONDS)!!.contains("client_hello"))
            assertFalse("Socket open is not protocol ready", events.connected.await(150, TimeUnit.MILLISECONDS))
            assertFalse(client.sendAudioChunk(byteArrayOf(1, 2), 2))
            assertNull(audio.poll(150, TimeUnit.MILLISECONDS))
            client.connect() // Duplicate callers must not create another socket.
            peer.send(helloV2)
            assertTrue(events.connected.await(3, TimeUnit.SECONDS))
            assertFalse(client.sendAudioChunk(byteArrayOf(1, 2), 2))
            assertNull(audio.poll(150, TimeUnit.MILLISECONDS))
            client.queryVoiceprint()
            val query = com.google.gson.JsonParser.parseString(hello.poll(3, TimeUnit.SECONDS)!!).asJsonObject
            assertEquals("voiceprint_query", query.get("type").asString)
            assertEquals("00000000-0000-4000-8000-000000000010", query.get("connection_id").asString)
            assertNotNull(query.get("request_id"))
        } finally {
            client.disconnect()
            peer.close(1000, "test done")
            server.shutdown()
        }
    }

    @Test fun authorizationDeniedRequiresSetupWithoutRetry() {
        for (status in listOf(401, 403)) {
            val server = MockWebServer()
            server.enqueue(MockResponse().setResponseCode(status))
            server.start()
            val events = Events()
            val client = NexoWebSocketClient(server.url("/").toString(), "test", "a".repeat(43), events)
            try {
                client.connect()
                assertTrue(events.setup.await(3, TimeUnit.SECONDS))
                assertEquals(NexoState.ERROR, events.state)
                client.connect() // Explicit callers cannot restart a terminal denied client either.
                Thread.sleep(2200) // Past the first normal backoff window.
                assertEquals(1, server.requestCount)
                assertFalse(client.sendAudioChunk(byteArrayOf(1, 2), 2))
            } finally { client.disconnect(); server.shutdown() }
        }
    }

    @Test fun policyCloseRequiresSetupWithoutRetry() {
        val server = MockWebServer()
        server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                webSocket.close(1008, "Device authorization required")
            }
        }))
        server.start()
        val events = Events()
        val client = NexoWebSocketClient(server.url("/").toString(), "test", "a".repeat(43), events)
        try {
            client.connect()
            assertTrue(events.setup.await(3, TimeUnit.SECONDS))
            client.connect()
            Thread.sleep(2200)
            assertEquals(1, server.requestCount)
            assertFalse(client.sendAudioChunk(byteArrayOf(1, 2), 2))
        } finally { client.disconnect(); server.shutdown() }
    }

    @Test fun rejectsEncodedOrWrongRateTtsBeforePlayback() {
        for (spec in listOf("\"format\":\"audio/mpeg\",\"sample_rate\":24000,\"channels\":1",
            "\"format\":\"audio/pcm\",\"sample_rate\":16000,\"channels\":1")) {
            val events = Events()
            val client = NexoWebSocketClient("ws://localhost/", "test", "a".repeat(43), events)
            try {
                val parse = client.javaClass.getDeclaredMethod("handleTextMessage", String::class.java)
                parse.isAccessible = true
                ready(client)
                parse.invoke(client, "{\"type\":\"tts_start\",$envelope,$spec}")
                assertEquals(0, events.ttsStarts)
                assertEquals(NexoState.ERROR, events.state)
            } finally { client.disconnect() }
        }
    }

    @Test fun acceptsDeclaredPcm24kMonoTts() {
        val events = Events()
        val client = NexoWebSocketClient("ws://localhost/", "test", "a".repeat(43), events)
        try {
            val parse = client.javaClass.getDeclaredMethod("handleTextMessage", String::class.java)
            parse.isAccessible = true
            ready(client)
            parse.invoke(client, """{"type":"tts_start",$envelope,"format":"audio/pcm","sample_rate":24000,"channels":1}""")
            assertEquals(1, events.ttsStarts)
        } finally { client.disconnect() }
    }

    @Test fun explicitNullStateFieldsStillDeliverState() {
        val events = Events()
        val client = NexoWebSocketClient("ws://localhost/", "test", "a".repeat(43), events)
        val parse = client.javaClass.getDeclaredMethod("handleTextMessage", String::class.java)
        parse.isAccessible = true
        ready(client)
        parse.invoke(client, """{"type":"state_change",$envelope,"state":"THINKING","message":null,"expression":null}""")
        assertEquals(NexoState.THINKING, events.state)
        assertNull(events.message)
        assertNull(events.expression)
        client.disconnect()
    }
}
