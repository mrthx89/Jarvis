package id.my.yhdev.jarvis.security

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.net.URI
import java.security.KeyStore
import java.util.Locale
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Owner-entered credential; only ciphertext/IV and its bound origin/device reach prefs. */
class DeviceCredentialStore(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences("device_credentials_v1", Context.MODE_PRIVATE)
    private val alias = "jarvis_device_credential_v1"

    fun origin(serverUrl: String): String? = runCatching {
        val uri = URI(serverUrl)
        require(uri.scheme?.lowercase(Locale.ROOT) == "wss")
        require(uri.rawUserInfo == null && uri.rawQuery == null && uri.rawFragment == null)
        val host = requireNotNull(uri.host).lowercase(Locale.ROOT)
        val port = if (uri.port == -1) 443 else uri.port
        require(port in 1..65535)
        "wss://$host:$port"
    }.getOrNull()

    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(alias, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true)
                .build())
        }.generateKey()
    }

    @Synchronized
    fun save(serverUrl: String, deviceId: String, token: String) {
        val boundOrigin = requireNotNull(origin(serverUrl))
        require(deviceId.matches(Regex("[A-Za-z0-9_-]{1,128}")))
        require(token.matches(Regex("[A-Za-z0-9_-]{43,128}")))
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key())
        cipher.updateAAD("$boundOrigin\n$deviceId".toByteArray(Charsets.UTF_8))
        val encrypted = cipher.doFinal(token.toByteArray(Charsets.UTF_8))
        check(prefs.edit().clear()
            .putString("origin", boundOrigin).putString("device_id", deviceId)
            .putString("iv", Base64.encodeToString(cipher.iv, Base64.NO_WRAP))
            .putString("ciphertext", Base64.encodeToString(encrypted, Base64.NO_WRAP)).commit())
    }

    @Synchronized
    fun read(serverUrl: String, deviceId: String): String? = runCatching {
        val requestedOrigin = origin(serverUrl) ?: return null
        val savedOrigin = prefs.getString("origin", null)
        if (savedOrigin != requestedOrigin || prefs.getString("device_id", null) != deviceId) return null
        val iv = Base64.decode(prefs.getString("iv", null) ?: return null, Base64.NO_WRAP)
        val ciphertext = Base64.decode(prefs.getString("ciphertext", null) ?: return null, Base64.NO_WRAP)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, iv))
        cipher.updateAAD("$requestedOrigin\n$deviceId".toByteArray(Charsets.UTF_8))
        String(cipher.doFinal(ciphertext), Charsets.UTF_8).takeIf { it.matches(Regex("[A-Za-z0-9_-]{43,128}")) }
    }.getOrNull() // Invalidated/restored keys fail closed: owner setup is required again.

    @Synchronized
    fun clear() {
        check(prefs.edit().clear().commit())
        KeyStore.getInstance("AndroidKeyStore").apply { load(null) }.deleteEntry(alias)
    }
}
