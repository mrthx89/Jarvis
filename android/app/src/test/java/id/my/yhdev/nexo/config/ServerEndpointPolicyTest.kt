package id.my.yhdev.nexo.config

import org.junit.Assert.*
import org.junit.Test

class ServerEndpointPolicyTest {
    @Test fun fallbackSkipsInvalidCandidatesWithoutThrowing() {
        val secureNexo = "wss://nexo.yhdev.my.id/ws/appliance"
        val secureNexo = "wss://nexo.yhdev.my.id/ws/appliance"
        assertEquals(secureNexo, ServerEndpointPolicy.fallback(secureNexo, "ws://invalid/ws"))
        assertEquals(secureNexo, ServerEndpointPolicy.fallback("ws://invalid/ws", secureNexo))
        assertEquals(secureNexo, ServerEndpointPolicy.fallback(secureNexo, "ws://invalid/ws"))
        assertNull(ServerEndpointPolicy.fallback(null, "ws://invalid/ws"))
        assertNull(ServerEndpointPolicy.fallback(null, ""))
    }

    @Test fun acceptsOnlyTlsWebSocketWithoutCredentialsOrFragments() {
        assertTrue(ServerEndpointPolicy.isValid("wss://nexo.yhdev.my.id/ws/appliance"))
        assertTrue(ServerEndpointPolicy.isValid("wss://nexo.yhdev.my.id/ws/appliance"))
        assertTrue(ServerEndpointPolicy.isValid("wss://example.org:8443/ws/appliance"))
        listOf("", "ws://example.org/ws", "https://example.org/ws", "wss://user:pass@example.org/ws",
            "wss://example.org/ws?token=secret", "wss://example.org/ws#fragment", "wss:///ws",
            "wss://example.org:99999/ws", "wss://example.org:0/ws", "wss://example.org /ws")
            .forEach { assertFalse(it, ServerEndpointPolicy.isValid(it)) }
    }
}
