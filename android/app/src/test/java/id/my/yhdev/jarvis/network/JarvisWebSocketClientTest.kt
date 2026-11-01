package id.my.yhdev.jarvis.network

import id.my.yhdev.jarvis.ui.JarvisState
import org.junit.Assert.*
import org.junit.Test
import okhttp3.*
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import okio.ByteString

class JarvisWebSocketClientTest {
    private class Events : JarvisWebSocketClient.WebSocketEventListener {
        var state: JarvisState? = null
        var message: String? = "not delivered"
        var expression: String? = "not delivered"
        val connected = CountDownLatch(1)
        val setup = CountDownLatch(1)
        override fun onConnected() { connected.countDown() }
        override fun onDisconnected() {}
        override fun onStateChanged(state: JarvisState, message: String?, expression: String?) {
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
        val client = JarvisWebSocketClient(server.url("/").toString(), "test", "a".repeat(43), events)
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
            peer.send("""{"type":"server_hello","session_id":"test","status":"IDLE"}""")
            assertTrue(events.connected.await(3, TimeUnit.SECONDS))
            client.sendAudioChunk(byteArrayOf(1, 2), 2)
            assertArrayEquals(byteArrayOf(1, 2), audio.poll(3, TimeUnit.SECONDS)!!.toByteArray())
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
            val client = JarvisWebSocketClient(server.url("/").toString(), "test", "a".repeat(43), events)
            try {
                client.connect()
                assertTrue(events.setup.await(3, TimeUnit.SECONDS))
                assertEquals(JarvisState.ERROR, events.state)
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
        val client = JarvisWebSocketClient(server.url("/").toString(), "test", "a".repeat(43), events)
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
            val client = JarvisWebSocketClient("ws://localhost/", "test", "a".repeat(43), events)
            try {
                val parse = client.javaClass.getDeclaredMethod("handleTextMessage", String::class.java)
                parse.isAccessible = true
                parse.invoke(client, "{\"type\":\"tts_start\",$spec}")
                assertEquals(0, events.ttsStarts)
                assertEquals(JarvisState.ERROR, events.state)
            } finally { client.disconnect() }
        }
    }

    @Test fun acceptsDeclaredPcm24kMonoTts() {
        val events = Events()
        val client = JarvisWebSocketClient("ws://localhost/", "test", "a".repeat(43), events)
        try {
            val parse = client.javaClass.getDeclaredMethod("handleTextMessage", String::class.java)
            parse.isAccessible = true
            parse.invoke(client, """{"type":"tts_start","format":"audio/pcm","sample_rate":24000,"channels":1}""")
            assertEquals(1, events.ttsStarts)
        } finally { client.disconnect() }
    }

    @Test fun explicitNullStateFieldsStillDeliverState() {
        val events = Events()
        val client = JarvisWebSocketClient("ws://localhost/", "test", "a".repeat(43), events)
        val parse = client.javaClass.getDeclaredMethod("handleTextMessage", String::class.java)
        parse.isAccessible = true
        parse.invoke(client, """{"type":"state_change","state":"THINKING","message":null,"expression":null}""")
        assertEquals(JarvisState.THINKING, events.state)
        assertNull(events.message)
        assertNull(events.expression)
        client.disconnect()
    }
}
