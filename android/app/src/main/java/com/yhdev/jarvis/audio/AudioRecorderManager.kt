package com.yhdev.jarvis.audio

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.util.Log
import java.util.concurrent.atomic.AtomicBoolean

class AudioRecorderManager(
    private val sampleRate: Int = 16000,
    private val onChunkCaptured: (ByteArray, Int) -> Unit
) {
    private val TAG = "AudioRecorderManager"
    private var audioRecord: AudioRecord? = null
    private var aec: AcousticEchoCanceler? = null
    private val isRecording = AtomicBoolean(false)
    private var recordingThread: Thread? = null

    // Chunk of 100ms: 16000 samples/sec * 2 bytes/sample * 0.1 sec = 3200 bytes
    private val bufferSize = 3200

    @SuppressLint("MissingPermission")
    fun startRecording(): Boolean {
        if (isRecording.get()) return true

        val minBufSize = AudioRecord.getMinBufferSize(
            sampleRate,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT
        )

        val actualBufSize = maxOf(minBufSize, bufferSize * 2)

        try {
            audioRecord = AudioRecord(
                MediaRecorder.AudioSource.VOICE_COMMUNICATION,
                sampleRate,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                actualBufSize
            )

            if (audioRecord?.state != AudioRecord.STATE_INITIALIZED) {
                Log.e(TAG, "AudioRecord initialization failed.")
                return false
            }

            // Enable Hardware Acoustic Echo Cancellation
            audioRecord?.audioSessionId?.let { sessionId ->
                aec = EchoCancellationHelper.attachAEC(sessionId)
            }

            audioRecord?.startRecording()
            isRecording.set(true)

            recordingThread = Thread({
                val chunk = ByteArray(bufferSize)
                while (isRecording.get()) {
                    val readBytes = audioRecord?.read(chunk, 0, bufferSize) ?: -1
                    if (readBytes > 0) {
                        onChunkCaptured(chunk, readBytes)
                    }
                }
            }, "Jarvis-AudioRecord-Thread").apply {
                priority = Thread.MAX_PRIORITY
                start()
            }

            Log.i(TAG, "Recording started at ${sampleRate}Hz.")
            return true
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start AudioRecord: ${e.message}", e)
            stopRecording()
            return false
        }
    }

    fun stopRecording() {
        isRecording.set(false)
        try {
            recordingThread?.join(500)
        } catch (ignored: InterruptedException) {}
        recordingThread = null

        try {
            aec?.release()
            aec = null
            audioRecord?.stop()
            audioRecord?.release()
            audioRecord = null
        } catch (e: Exception) {
            Log.w(TAG, "Error cleaning up AudioRecord: ${e.message}")
        }
        Log.i(TAG, "Recording stopped.")
    }

    fun isRecording(): Boolean = isRecording.get()
}
