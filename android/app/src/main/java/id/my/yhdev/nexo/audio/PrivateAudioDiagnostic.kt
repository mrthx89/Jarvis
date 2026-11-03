package id.my.yhdev.nexo.audio

import android.content.Context
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.os.SystemClock
import id.my.yhdev.nexo.audio.core.PrivateDiagnosticClip
import id.my.yhdev.nexo.audio.core.UtteranceBuffer
import java.io.OutputStream
import java.util.concurrent.CompletableFuture
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/** Opt-in private local playback/export, never a normal conversation or upload path. */
class PrivateAudioDiagnostic internal constructor(privateDirectory: java.io.File) : AutoCloseable {
    constructor(context: Context) : this(context.noBackupFilesDir.resolve("audio-diagnostic"))
    private val worker = Executors.newSingleThreadScheduledExecutor { task ->
        Thread(task, "Nexo-PrivateDiagnostic").apply { isDaemon = true }
    }
    private val clip = PrivateDiagnosticClip(privateDirectory)
    private val admission = java.util.concurrent.Semaphore(1)
    private var player: MediaPlayer? = null
    private val playbackGeneration = java.util.concurrent.atomic.AtomicLong()
    /** Atomic admission revocation; an admitted start is quiesced by dismiss on the worker. */
    fun fencePlayback() { playbackGeneration.incrementAndGet() }
    private var expiresAt = 0L
    init {
        worker.scheduleAtFixedRate({
            runCatching {
                val now = SystemClock.elapsedRealtime()
                if (expiresAt != 0L && now >= expiresAt) { stopPlayback(); clip.close(); expiresAt = 0 }
            }.onFailure { stopPlayback() }
        }, 100, 100, TimeUnit.MILLISECONDS)
    }
    private fun <T> submit(action: () -> T): CompletableFuture<T> {
        val result = CompletableFuture<T>()
        if (!admission.tryAcquire()) {
            result.completeExceptionally(IllegalStateException("diagnostic_busy"))
            return result
        }
        runCatching { worker.execute {
            try { result.complete(action()) } catch (e: Exception) { result.completeExceptionally(e) }
            finally { admission.release() }
        } }.onFailure { admission.release(); result.completeExceptionally(it) }
        return result
    }
    fun retain(pcm: UtteranceBuffer.Sealed, explicitConsent: Boolean): CompletableFuture<Boolean> = submit {
        stopPlayback()
        val now = SystemClock.elapsedRealtime()
        clip.retain(pcm, explicitConsent, now).also { if (it) expiresAt = now + 300000 }
    }
    /** Requires active diagnostic ownership and all normal playback/capture gates closed by the service. */
    fun play(explicitAction: Boolean): CompletableFuture<Boolean> {
        val token = playbackGeneration.get()
        return submit {
        if (!explicitAction) return@submit false
        stopPlayback()
        val input = clip.openPlayback(SystemClock.elapsedRealtime()) ?: return@submit false
        try {
            val current = MediaPlayer()
            player = current
            current.setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
            current.setDataSource(input.fd)
            current.setOnCompletionListener { runCatching { worker.execute { stopPlayback(current) } } }
            current.setOnErrorListener { _, _, _ -> runCatching { worker.execute { stopPlayback(current) } }; true }
            current.prepare()
            if (token != playbackGeneration.get()) { stopPlayback(current); false }
            else { current.start(); true }
        } catch (_: Exception) { stopPlayback(); false }
        finally { input.close() }
        }
    }
    /** UI must warn that external copies cannot be erased, then open the destination explicitly. */
    fun exportWav(destination: OutputStream, explicitConsent: Boolean): Boolean {
        // Caller-owned I/O worker: external storage must never delay private TTL deletion.
        return clip.exportWav(destination, explicitConsent, SystemClock.elapsedRealtime())
    }
    fun dismiss(): CompletableFuture<Unit> {
        fencePlayback()
        val result = CompletableFuture<Unit>()
        worker.execute {
            try { stopPlayback(); clip.close(); expiresAt = 0; result.complete(Unit) }
            catch (e: Exception) { result.completeExceptionally(e) }
        }
        return result
    }
    private fun stopPlayback(expected: MediaPlayer? = player) {
        if (player !== expected) return
        player = null
        expected?.let { runCatching { it.stop() }; it.release() }
    }
    /** Call on lifecycle worker, not UI/read thread. Also call dismiss on background/route/owner exit. */
    override fun close() {
        try { dismiss().get(2, TimeUnit.SECONDS) }
        finally { worker.shutdownNow() }
    }
}
