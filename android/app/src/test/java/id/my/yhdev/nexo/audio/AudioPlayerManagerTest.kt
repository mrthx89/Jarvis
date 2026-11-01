package id.my.yhdev.nexo.audio

import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicInteger

class AudioPlayerManagerTest {
    @Test fun drainTimeoutIsFailureNotSuccessfulDrain() {
        val worker = Executors.newSingleThreadExecutor()
        val failed = CountDownLatch(1)
        val drained = AtomicInteger()
        var now = 0L
        val player = AudioPlayerManager(worker = worker, outputFactory = {
            object : PcmOutput {
                override val playbackHeadFrames = 0L
                override fun write(data: ByteArray, offset: Int, length: Int) = length
                override fun close() {}
            }
        }, nowNanos = { now }, sleep = { now += 6_000_000_000L },
            onError = { failed.countDown() })
        try {
            player.writeChunk(ByteArray(4))
            player.finish { drained.incrementAndGet() }
            assertTrue(failed.await(2, TimeUnit.SECONDS))
        } finally {
            player.release()
        }
        assertTrue(worker.awaitTermination(2, TimeUnit.SECONDS))
        assertEquals(0, drained.get())
        assertFalse(player.isPlaying())
    }

    @Test fun releaseCancelsBackpressuredWriteAndRunsCleanup() {
        val worker = Executors.newSingleThreadExecutor()
        val attempted = CountDownLatch(1)
        val closed = CountDownLatch(1)
        val errors = AtomicInteger()
        val player = AudioPlayerManager(worker = worker, outputFactory = {
            object : PcmOutput {
                override val playbackHeadFrames = 0L
                override fun write(data: ByteArray, offset: Int, length: Int): Int {
                    attempted.countDown()
                    return 0
                }
                override fun close() { closed.countDown() }
            }
        }, onError = { errors.incrementAndGet() })
        try {
            player.writeChunk(ByteArray(4))
            assertTrue(attempted.await(2, TimeUnit.SECONDS))
        } finally {
            player.release()
        }
        assertTrue(worker.awaitTermination(2, TimeUnit.SECONDS))
        assertEquals(0L, closed.count)
        assertEquals(0, errors.get())
    }

    @Test fun writeFailureInvalidatesQueuedFinishAndNotifiesOnlyOnce() {
        val writeEntered = CountDownLatch(1)
        val allowFailure = CountDownLatch(1)
        val failed = CountDownLatch(1)
        val errors = AtomicInteger()
        val drained = AtomicInteger()
        val closed = CountDownLatch(1)
        val worker = Executors.newSingleThreadExecutor()
        val player = AudioPlayerManager(worker = worker, outputFactory = {
            object : PcmOutput {
                override val playbackHeadFrames = 0L
                override fun write(data: ByteArray, offset: Int, length: Int): Int {
                    writeEntered.countDown()
                    check(allowFailure.await(2, TimeUnit.SECONDS))
                    return -6
                }
                override fun close() { closed.countDown() }
            }
        }, onError = { errors.incrementAndGet(); failed.countDown() })
        try {
            player.writeChunk(ByteArray(4))
            assertTrue(writeEntered.await(2, TimeUnit.SECONDS))
            player.finish { drained.incrementAndGet() }
            player.writeChunk(ByteArray(4))
            allowFailure.countDown()
            assertTrue(failed.await(2, TimeUnit.SECONDS))
            // A failed player rejects even newly queued input until explicit stop/reconnect.
            player.finish { drained.incrementAndGet() }
            player.release()
            assertTrue(worker.awaitTermination(2, TimeUnit.SECONDS))
            assertEquals(1, errors.get())
            assertEquals(0, drained.get())
            assertEquals(0, closed.count)
            assertFalse(player.isPlaying())
        } finally {
            allowFailure.countDown()
            player.release()
        }
    }

    @Test fun boundedQueueRejectsExcessChunksToPreventLatencyBuildup() {
        val worker = Executors.newSingleThreadExecutor()
        val writeBlocked = CountDownLatch(1)
        val player = AudioPlayerManager(
            worker = worker,
            maxBufferedBytes = 16,
            outputFactory = {
                object : PcmOutput {
                    override val playbackHeadFrames = 0L
                    override fun write(data: ByteArray, offset: Int, length: Int): Int {
                        writeBlocked.await(500, TimeUnit.MILLISECONDS)
                        return length
                    }
                    override fun close() {}
                }
            }
        )
        try {
            assertTrue(player.writeChunk(ByteArray(10)))
            // Exceeds maxBufferedBytes (10 + 10 > 16)
            assertFalse(player.writeChunk(ByteArray(10)))
        } finally {
            writeBlocked.countDown()
            player.release()
            worker.awaitTermination(2, TimeUnit.SECONDS)
        }
    }
}
