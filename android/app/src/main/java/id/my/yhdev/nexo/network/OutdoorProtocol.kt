package id.my.yhdev.nexo.network

import com.google.gson.JsonObject
import id.my.yhdev.nexo.audio.core.UploadSession

data class OutdoorSession(
    val connectionId: String,
    val serverInstanceId: String,
    val outputEpoch: Long,
    val maxChunkBytes: Int
)

object OutdoorProtocol {
    fun string(json: JsonObject, key: String): String? = json.get(key)?.let {
        if (it.isJsonPrimitive && it.asJsonPrimitive.isString) it.asString else null
    }

    fun integer(json: JsonObject, key: String, minimum: Long, maximum: Long): Long? {
        val value = json.get(key) ?: return null
        if (!value.isJsonPrimitive || !value.asJsonPrimitive.isNumber) return null
        val text = value.asString
        if (!text.matches(Regex("0|[1-9][0-9]*"))) return null
        return text.toLongOrNull()?.takeIf { it in minimum..maximum }
    }

    fun uuid(json: JsonObject, key: String): String? = string(json, key)?.takeIf {
        runCatching { UploadSession.uuid(it); true }.getOrDefault(false)
    }

    fun negotiate(json: JsonObject): OutdoorSession? {
        val capabilities = json.getAsJsonArray("capabilities")?.mapNotNull {
            if (it.isJsonPrimitive && it.asJsonPrimitive.isString) it.asString else null
        } ?: return null
        if (!capabilities.containsAll(listOf("utterance_upload_v2", "exclusive_audio_v1"))) return null
        if (string(json, "protocol_version") != "2.0") return null
        if (json.get("conversation_paused")?.let { it.isJsonPrimitive && it.asJsonPrimitive.isBoolean && it.asBoolean } != true) return null
        val connection = uuid(json, "connection_id") ?: return null
        val instance = uuid(json, "server_instance_id") ?: return null
        val epoch = integer(json, "output_epoch", 0, Long.MAX_VALUE) ?: return null
        val limits = json.getAsJsonObject("limits") ?: return null
        if (integer(limits, "max_samples", 320000, 320000) == null ||
            integer(limits, "retry_ttl_ms", 30000, 30000) == null ||
            integer(limits, "owner_lease_ms", 15000, 15000) == null) return null
        val chunk = integer(limits, "max_chunk_bytes", 2, 16384)?.toInt() ?: return null
        if (chunk % 2 != 0) return null
        return OutdoorSession(connection, instance, epoch, chunk)
    }
}
