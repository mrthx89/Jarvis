package id.my.yhdev.nexo.audio

import org.junit.Assert.*
import org.junit.Test

class PcmWritePumpTest {
    @Test fun cancellationAfterBackpressureReturnsWithoutAnotherWrite() {
        var current = true
        var attempts = 0
        writePcmChunk(ByteArray(4), { current }, { _, _, _ -> attempts++; 0 },
            sleep = { current = false }, onWritten = { fail("No bytes were written") })
        assertEquals(1, attempts)
    }

    @Test fun partialWritesPreserveOffsetsAndCountOnlyAcceptedBytes() {
        val offsets = mutableListOf<Int>()
        var accepted = 0
        writePcmChunk(ByteArray(8), { true }, { _, offset, _ -> offsets += offset; 2 },
            onWritten = { accepted += it })
        assertEquals(listOf(0, 2, 4, 6), offsets)
        assertEquals(8, accepted)
    }

    @Test fun negativeWriteIsTerminal() {
        try {
            writePcmChunk(ByteArray(4), { true }, { _, _, _ -> -6 }, onWritten = {})
            fail("Device errors must terminate the write")
        } catch (expected: IllegalStateException) {
            assertEquals("AudioTrack.write returned -6", expected.message)
        }
    }

    @Test fun stalledWriteTimesOutWithoutBusySpin() {
        var now = 0L
        var attempts = 0
        var sleeps = 0
        try {
            writePcmChunk(ByteArray(4), { true }, { _, _, _ -> attempts++; 0 },
                nowNanos = { now }, sleep = { ms -> sleeps++; now += ms * 1_000_000 },
                timeoutMs = 30, onWritten = {})
            fail("A stalled output must terminate")
        } catch (expected: IllegalStateException) {
            assertEquals("Audio write stalled", expected.message)
        }
        assertEquals(3, sleeps)
        assertEquals(3, attempts)
    }
}
