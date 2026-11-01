package id.my.yhdev.jarvis.config

import java.net.URI

/** Production audio and credentials must travel over TLS, never in URLs. */
object ServerEndpointPolicy {
    fun isValid(value: String): Boolean = runCatching {
        val uri = URI(value)
        uri.scheme == "wss" && !uri.host.isNullOrBlank() &&
            uri.rawUserInfo == null && uri.rawQuery == null && uri.rawFragment == null &&
            (uri.port == -1 || uri.port in 1..65535)
    }.getOrDefault(false)
}
