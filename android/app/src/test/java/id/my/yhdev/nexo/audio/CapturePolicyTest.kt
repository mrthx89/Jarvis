package id.my.yhdev.nexo.audio

import org.junit.Assert.*
import org.junit.Test

// Source-only until the owner runs Android/JVM tests; no agent Android build.
class CapturePolicyTest {
    @Test fun baselineDoesNotRequestStackedEffects() {
        val baseline = CaptureOptions()
        assertEquals(CaptureSource.VOICE_RECOGNITION, baseline.source)
        assertFalse(baseline.requestAec)
        assertFalse(baseline.requestNs)
    }

    @Test fun legacyComparisonRetainsRequestedAecAndNs() {
        val legacy = CaptureOptions.legacyComparison()
        assertEquals(CaptureSource.VOICE_COMMUNICATION, legacy.source)
        assertTrue(legacy.requestAec)
        assertTrue(legacy.requestNs)
    }
}
