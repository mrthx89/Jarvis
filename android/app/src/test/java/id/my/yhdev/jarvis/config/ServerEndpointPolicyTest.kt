package id.my.yhdev.jarvis.config

import org.junit.Assert.*
import org.junit.Test

class ServerEndpointPolicyTest {
    @Test fun acceptsOnlyTlsWebSocketWithoutCredentialsOrFragments() {
        assertTrue(ServerEndpointPolicy.isValid("wss://jarvis.yhdev.my.id/ws/appliance"))
        assertTrue(ServerEndpointPolicy.isValid("wss://example.org:8443/ws/appliance"))
        listOf("", "ws://example.org/ws", "https://example.org/ws", "wss://user:pass@example.org/ws",
            "wss://example.org/ws?token=secret", "wss://example.org/ws#fragment", "wss:///ws",
            "wss://example.org:99999/ws", "wss://example.org:0/ws", "wss://example.org /ws")
            .forEach { assertFalse(it, ServerEndpointPolicy.isValid(it)) }
    }
}
