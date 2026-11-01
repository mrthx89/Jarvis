package id.my.yhdev.jarvis.audio

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.util.Log
import java.util.concurrent.atomic.AtomicBoolean

class AudioRecorderManager(
    private val sampleRate: Int = 16000,
    private val onError: (String) -> Unit = {},
    private val onChunkCaptured: (ByteArray, Int) -> Unit
) {
    private var audioRecord: AudioRecord? = null
    private var aec: AcousticEchoCanceler? = null
    private val isRecording = AtomicBoolean(false)
    private var recordingThread: Thread? = null
    private val bufferSize = 3200 // 100 ms of 16 kHz, PCM16, mono

    @SuppressLint("MissingPermission")
    @Synchronized
    fun startRecording(): Boolean {
        if (isRecording.get()) return true
        try {
            val minBuffer = AudioRecord.getMinBufferSize(sampleRate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
            check(minBuffer > 0) { "Unsupported microphone PCM format" }
            val record = AudioRecord(MediaRecorder.AudioSource.VOICE_COMMUNICATION, sampleRate,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, maxOf(minBuffer, bufferSize * 2))
            audioRecord = record
            check(record.state == AudioRecord.STATE_INITIALIZED) { "AudioRecord initialization failed" }
            check(record.sampleRate == sampleRate && record.channelCount == 1 &&
                record.audioFormat == AudioFormat.ENCODING_PCM_16BIT) { "Microphone PCM format mismatch" }
            aec = EchoCancellationHelper.attachAEC(record.audioSessionId)
            record.startRecording()
            check(record.recordingState == AudioRecord.RECORDSTATE_RECORDING) { "Microphone did not start" }
            isRecording.set(true)
            recordingThread = Thread({
                val chunk = ByteArray(bufferSize)
                try {
                    while (isRecording.get()) {
                        val count = record.read(chunk, 0, chunk.size)
                        if (!isRecording.get()) break
                        check(count > 0) { "AudioRecord.read returned $count" }
                        onChunkCaptured(chunk, count)
                    }
                } catch (e: Exception) {
                    if (isRecording.getAndSet(false)) onError("Mikrofon terhenti: ${e.message}")
                }
            }, "Jarvis-AudioRecord-Thread").apply { start() }
            return true
        } catch (e: Exception) {
            Log.e("AudioRecorderManager", "Failed to start microphone: ${e.message}", e)
            stopRecording()
            return false
        }
    }

    @Synchronized
    fun stopRecording() {
        isRecording.set(false)
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

    fun isRecording(): Boolean = isRecording.get()
}
