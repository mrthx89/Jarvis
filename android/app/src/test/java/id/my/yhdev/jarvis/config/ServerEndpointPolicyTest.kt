package id.my.yhdev.jarvis.config

import org.junit.Assert.*
import org.junit.Test

class ServerEndpointPolicyTest {
    @Test fun fallbackSkipsInvalidCandidatesWithoutThrowing() {
        val secure = "wss://jarvis.yhdev.my.id/ws/appliance"
        assertEquals(secure, ServerEndpointPolicy.fallback(secure, "ws://invalid/ws"))
        assertEquals(secure, ServerEndpointPolicy.fallback("ws://invalid/ws", secure))
        assertNull(ServerEndpointPolicy.fallback(null, "ws://invalid/ws"))
        assertNull(ServerEndpointPolicy.fallback(null, ""))
    }

    @Test fun acceptsOnlyTlsWebSocketWithoutCredentialsOrFragments() {
        assertTrue(ServerEndpointPolicy.isValid("wss://jarvis.yhdev.my.id/ws/appliance"))
        assertTrue(ServerEndpointPolicy.isValid("wss://example.org:8443/ws/appliance"))
        listOf("", "ws://example.org/ws", "https://example.org/ws", "wss://user:pass@example.org/ws",
            "wss://example.org/ws?token=secret", "wss://example.org/ws#fragment", "wss:///ws",
            "wss://example.org:99999/ws", "wss://example.org:0/ws", "wss://example.org /ws")
            .forEach { assertFalse(it, ServerEndpointPolicy.isValid(it)) }
    }
}
