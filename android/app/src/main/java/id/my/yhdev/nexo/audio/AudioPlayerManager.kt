package id.my.yhdev.nexo.audio

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.util.Log
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

/** Nonblocking PCM pump; a stalled device must never hold up queued stop/release. */
internal fun writePcmChunk(
    data: ByteArray,
    isCurrent: () -> Boolean,
    write: (ByteArray, Int, Int) -> Int,
    nowNanos: () -> Long = System::nanoTime,
    sleep: (Long) -> Unit = Thread::sleep,
    timeoutMs: Long = 5000,
    onWritten: (Int) -> Unit
) {
    check(data.size % 2 == 0) { "PCM16 chunk must contain whole samples" }
    var offset = 0
    var lastProgress = nowNanos()
    while (offset < data.size && isCurrent()) {
        check((nowNanos() - lastProgress) / 1_000_000 < timeoutMs) { "Audio write stalled" }
        val written = write(data, offset, minOf(data.size - offset, 9600))
        if (!isCurrent()) return
        check(written >= 0) { "AudioTrack.write returned $written" }
        if (written == 0) {
            sleep(10)
        } else {
            check(written <= data.size - offset && written % 2 == 0) { "Invalid PCM write count: $written" }
            offset += written
            onWritten(written)
            lastProgress = nowNanos()
        }
    }
}

/** Hardware seam: write must return immediately, including when the output buffer is full. */
internal interface PcmOutput {
    val playbackHeadFrames: Long
    fun write(data: ByteArray, offset: Int, length: Int): Int
    fun close()
}

private fun createPcmOutput(sampleRate: Int): PcmOutput {
    val minBuffer = AudioTrack.getMinBufferSize(sampleRate, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT)
    check(minBuffer > 0) { "Unsupported PCM output" }
    val track = AudioTrack.Builder()
        .setAudioAttributes(AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_ASSISTANT)
            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
        .setAudioFormat(AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_16BIT)
            .setSampleRate(sampleRate).setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
        .setBufferSizeInBytes(maxOf(minBuffer, 9600))
        .setTransferMode(AudioTrack.MODE_STREAM).build()
    try { track.play() } catch (e: Exception) { track.release(); throw e }
    return object : PcmOutput {
        override val playbackHeadFrames: Long
            get() = track.playbackHeadPosition.toLong() and 0xffffffffL
        override fun write(data: ByteArray, offset: Int, length: Int): Int =
            track.write(data, offset, length, AudioTrack.WRITE_NON_BLOCKING)
        override fun close() {
            try { track.pause(); track.flush(); track.stop() }
            finally { track.release() }
        }
    }
}

/** Server output is raw PCM signed 16-bit little endian, 24 kHz, mono (not MP3/WAV). */
class AudioPlayerManager internal constructor(
    private val sampleRate: Int = 24000,
    private val onError: (String) -> Unit = {},
    private val worker: ExecutorService = Executors.newSingleThreadExecutor(),
    private val outputFactory: () -> PcmOutput = { createPcmOutput(sampleRate) },
    private val nowNanos: () -> Long = System::nanoTime,
    private val sleep: (Long) -> Unit = Thread::sleep
) {
    private var output: PcmOutput? = null
    private val generation = AtomicInteger(0)
    private val released = AtomicBoolean(false)
    @Volatile private var isPlaying = false
    private var failed = false // worker-confined; only explicit stop/reconnect resets failure
    private var framesWritten = 0L

    private fun startTrack() {
        if (output != null) return
        output = outputFactory()
        framesWritten = 0
        isPlaying = true
    }

    private fun current(token: Int): Boolean = token == generation.get() && !failed

    private fun fail(token: Int, error: Exception) {
        stopTrack()
        // Invalidate every queued write/drain from this failed turn, not just this operation.
        if (generation.compareAndSet(token, token + 1)) {
            failed = true
            onError("Pemutaran audio gagal: ${error.message}")
        }
    }

    fun start() {
        val token = generation.get()
        worker.execute {
            if (!current(token)) return@execute
            try { startTrack() } catch (e: Exception) { fail(token, e) }
        }
    }

    fun writeChunk(data: ByteArray) {
        val token = generation.get()
        worker.execute {
            if (!current(token)) return@execute
            try {
                startTrack()
                writePcmChunk(data, { current(token) }, output!!::write, nowNanos, sleep,
                    onWritten = { framesWritten += it / 2 })
            } catch (e: Exception) {
                fail(token, e)
            }
        }
    }

    /** tts_end means no more network data, not that queued hardware audio has finished. */
    fun finish(onDrained: () -> Unit) {
        val token = generation.get()
        worker.execute {
            if (!current(token)) return@execute
            try {
                val track = output
                if (track != null) {
                    val started = nowNanos()
                    val remaining = maxOf(0L, framesWritten - track.playbackHeadFrames)
                    val timeoutMs = remaining * 1000L / sampleRate + 5000L
                    while (current(token) && track.playbackHeadFrames < framesWritten) {
                        check((nowNanos() - started) / 1_000_000 < timeoutMs) { "Audio playback stalled" }
                        sleep(10)
                    }
                }
                stopTrack()
                if (current(token)) onDrained()
            } catch (e: Exception) {
                fail(token, e)
            }
        }
    }

    private fun stopTrack() {
        val track = output
        output = null
        isPlaying = false
        framesWritten = 0
        try { track?.close() }
        catch (e: Exception) { Log.w("AudioPlayerManager", "Error stopping AudioTrack: ${e.message}") }
    }

    fun stop() {
        if (released.get()) return
        generation.incrementAndGet()
        worker.execute { stopTrack(); failed = false }
    }

    fun release() {
        if (!released.compareAndSet(false, true)) return
        generation.incrementAndGet()
        worker.execute { stopTrack() }
        worker.shutdown()
    }

    fun isPlaying(): Boolean = isPlaying
}
