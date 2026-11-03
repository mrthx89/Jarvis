package id.my.yhdev.nexo.audio

import android.media.MediaRecorder

enum class CaptureSource(val value: Int) {
    VOICE_RECOGNITION(MediaRecorder.AudioSource.VOICE_RECOGNITION),
    UNPROCESSED(MediaRecorder.AudioSource.UNPROCESSED),
    VOICE_COMMUNICATION(MediaRecorder.AudioSource.VOICE_COMMUNICATION)
}

data class CaptureOptions(
    val source: CaptureSource = CaptureSource.VOICE_RECOGNITION,
    val requestAec: Boolean = false,
    val requestNs: Boolean = false,
    val automaticVad: Boolean = false
) {
    companion object {
        fun legacyComparison() = CaptureOptions(CaptureSource.VOICE_COMMUNICATION, true, true)
    }
}

data class CaptureDiagnostics(
    val requestedSource: CaptureSource,
    val selectedSource: CaptureSource,
    val fallbackReason: String?,
    val sampleRate: Int,
    val channels: Int,
    val encoding: Int,
    val routeType: Int?,
    val routeId: Int?,
    val aecAvailable: Boolean,
    val aecRequested: Boolean,
    val aecEnabled: Boolean,
    val nsAvailable: Boolean,
    val nsRequested: Boolean,
    val nsEnabled: Boolean
)
