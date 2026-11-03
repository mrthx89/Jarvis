package id.my.yhdev.nexo.audio

import id.my.yhdev.nexo.audio.core.UtteranceBuffer
import org.junit.Assert.*
import org.junit.Test
import java.nio.file.Files

/** Source only: Kotlin/Android compilation and execution are prohibited for the agent. */
class PrivateAudioDiagnosticTest {
    @Test fun requiresExplicitConsentAndDismissDeletesClip() {
        val directory = Files.createTempDirectory("nexo-test-diagnostic").toFile()
        val buffer = UtteranceBuffer()
        buffer.start("diagnostic", 0)
        buffer.accept(byteArrayOf(1, 2), 2)
        val pcm = buffer.finish("manual_finish", 0)!!
        val diagnostic = PrivateAudioDiagnostic(directory)
        try {
            assertFalse(diagnostic.retain(pcm, false).get())
            assertTrue(diagnostic.retain(pcm, true).get())
            assertTrue(directory.resolve("local-test.wav").exists())
            diagnostic.dismiss().get()
            assertFalse(directory.resolve("local-test.wav").exists())
        } finally { diagnostic.close(); directory.delete() }
    }
}
