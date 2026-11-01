package id.my.yhdev.nexo.audio

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.util.Log
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

class AudioRecorderManager(
    private val sampleRate: Int = 16000,
    private val onError: (String) -> Unit = {},
    private val onRecoverableError: (String) -> Unit = onError,
    private val onChunkCaptured: (ByteArray, Int) -> Unit
) {
    private var audioRecord: AudioRecord? = null
    private var aec: AcousticEchoCanceler? = null
    @Volatile private var recordingActive = AtomicBoolean(false)
    private val recordingGeneration = AtomicInteger(0)
    private class RecoverableReadException(count: Int) : IllegalStateException("AudioRecord.read returned $count")
    private var recordingThread: Thread? = null
    private val bufferSize = 3200 // 100 ms of 16 kHz, PCM16, mono

    @SuppressLint("MissingPermission")
    @Synchronized
    fun startRecording(): Boolean {
        if (recordingActive.get()) return true
        stopRecording() // Release a previously failed reader before replacing its resources.
        val session = recordingGeneration.incrementAndGet()
        try {
            val minBuffer = AudioRecord.getMinBufferSize(sampleRate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
            require(minBuffer > 0) { "Unsupported microphone PCM format" }
            val record = AudioRecord(MediaRecorder.AudioSource.VOICE_COMMUNICATION, sampleRate,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, maxOf(minBuffer, bufferSize * 2))
            audioRecord = record
            check(record.state == AudioRecord.STATE_INITIALIZED) { "AudioRecord initialization failed" }
            require(record.sampleRate == sampleRate && record.channelCount == 1 &&
                record.audioFormat == AudioFormat.ENCODING_PCM_16BIT) { "Microphone PCM format mismatch" }
            aec = EchoCancellationHelper.attachAEC(record.audioSessionId)
            record.startRecording()
            check(record.recordingState == AudioRecord.RECORDSTATE_RECORDING) { "Microphone did not start" }
            val active = AtomicBoolean(true)
            recordingActive = active
            recordingThread = Thread({
                val chunk = ByteArray(bufferSize)
                try {
                    while (active.get() && session == recordingGeneration.get()) {
                        val count = record.read(chunk, 0, chunk.size)
                        if (!active.get() || session != recordingGeneration.get()) break
                        if (count == AudioRecord.ERROR_DEAD_OBJECT || count == AudioRecord.ERROR_INVALID_OPERATION) {
                            throw RecoverableReadException(count)
                        }
                        check(count > 0) { "AudioRecord.read returned $count" }
                        onChunkCaptured(chunk, count)
                    }
                } catch (e: Exception) {
                    if (session == recordingGeneration.get() && active.getAndSet(false)) {
                        val message = "Mikrofon terhenti: ${e.message}"
                        if (e is RecoverableReadException) onRecoverableError(message) else onError(message)
                    }
                }
            }, "Nexo-AudioRecord-Thread").apply { start() }
            return true
        } catch (e: Exception) {
            Log.e("AudioRecorderManager", "Failed to start microphone: ${e.message}", e)
            stopRecording()
            val message = "Mikrofon gagal dimulai: ${e.message}"
            if (e is SecurityException || e is IllegalArgumentException) onError(message)
            else onRecoverableError(message)
            return false
        }
    }

    @Synchronized
    fun stopRecording() {
        recordingGeneration.incrementAndGet()
        recordingActive.set(false)
        val record = audioRecord
        audioRecord = null
        // Stop first: read() can block, so joining before stop leaves a live reader.
        try { record?.stop() } catch (_: Exception) {}
        val thread = recordingThread
        recordingThread = null
        if (thread != null && thread !== Thread.currentThread()) {
            try { thread.join(1000) } catch (_: InterruptedException) { Thread.currentThread().interrupt() }
        }
        try { aec?.release() } catch (_: Exception) {}
        aec = null
        try { record?.release() } catch (_: Exception) {}
    }

    fun isRecording(): Boolean = recordingActive.get()
}
