package id.my.yhdev.nexo.audio

import com.google.gson.JsonParser
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.MessageDigest
import java.util.UUID
import org.junit.Assert.*
import org.junit.Test

/** Golden contract data checks only, not a test of the future capture/lease implementation. */
class OutdoorAudioFixtureTest {
    private fun fixture() = generateSequence(File(System.getProperty("user.dir"))) { it.parentFile }
        .map { File(it, "docs/fixtures/outdoor_audio_v2.json") }
        .firstOrNull { it.isFile }
        ?.let { JsonParser.parseString(it.readText()).asJsonObject }
        ?: error("Run from a Nexo checkout; canonical outdoor fixture missing")

    private fun decodeHex(hex: String): ByteArray {
        require(hex.length % 2 == 0)
        return hex.chunked(2).map { it.toInt(16).toByte() }.toByteArray()
    }

    @Test fun binaryHeaderAndPcmMatchSharedPythonGoldenVector() {
        val upload = fixture().getAsJsonObject("upload")
        val begin = upload.getAsJsonObject("begin")
        val id = UUID.fromString(begin.get("utterance_id").asString)
        val pcm = decodeHex(upload.get("pcm_hex").asString)
        val encoded = ByteBuffer.allocate(28 + pcm.size).order(ByteOrder.BIG_ENDIAN)
            .put(byteArrayOf(0x4e, 0x58, 0x41, 0x32))
            .putLong(id.mostSignificantBits).putLong(id.leastSignificantBits)
            .putInt(0).putInt(pcm.size).put(pcm).array()
        assertArrayEquals(decodeHex(upload.getAsJsonArray("frames_hex")[0].asString), encoded)
        assertEquals(begin.get("total_samples").asInt * 2, pcm.size)
        val digest = MessageDigest.getInstance("SHA-256").digest(pcm)
            .joinToString("") { "%02x".format(it.toInt() and 0xff) }
        assertEquals(begin.get("sha256").asString, digest)
        val samples = ByteBuffer.wrap(pcm).order(ByteOrder.LITTLE_ENDIAN)
        assertEquals(0, samples.short.toInt())
        assertEquals(1, samples.short.toInt())
        assertEquals(-1, samples.short.toInt())
        assertEquals(32767, samples.short.toInt())
        assertEquals(-32768, samples.short.toInt())
    }

    @Test fun ownershipFixturesIncludeMutedCaptureAndNoImplicitResume() {
        val traces = fixture().getAsJsonArray("ownership_traces")
            .associate { it.asJsonObject.get("name").asString to it.asJsonObject }
        assertTrue(traces.getValue("enrollment_while_muted").getAsJsonObject("expect")
            .get("dedicated_mic").asBoolean)
        assertFalse(traces.getValue("saved_does_not_resume").getAsJsonObject("expect")
            .get("normal_mic").asBoolean)
        assertFalse(traces.getValue("release_before_grant").getAsJsonObject("expect")
            .get("ready").asBoolean)
    }
}
