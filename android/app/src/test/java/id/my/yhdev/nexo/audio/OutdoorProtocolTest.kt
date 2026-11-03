package id.my.yhdev.nexo.audio

import com.google.gson.JsonParser
import id.my.yhdev.nexo.network.OutdoorProtocol
import org.junit.Assert.*
import org.junit.Test

class OutdoorProtocolTest {
    @Test fun absentCapabilityFailsClosed() {
        assertNull(OutdoorProtocol.negotiate(JsonParser.parseString("""{"type":"server_hello"}""").asJsonObject))
    }

    @Test fun booleanEpochIsNotAnInteger() {
        val message = JsonParser.parseString("""{"output_epoch":true}""").asJsonObject
        assertNull(OutdoorProtocol.integer(message, "output_epoch", 0, Long.MAX_VALUE))
    }

    @Test fun fractionalAndStringCountersAreRejected() {
        for (value in listOf("1.2", "\"12\"", "-1", "9223372036854775808")) {
            val message = JsonParser.parseString("""{"next_sample":$value}""").asJsonObject
            assertNull(OutdoorProtocol.integer(message, "next_sample", 0, 320000))
        }
    }
}
