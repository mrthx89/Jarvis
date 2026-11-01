package id.my.yhdev.nexo.audio

import android.media.audiofx.AcousticEchoCanceler
import android.util.Log

object EchoCancellationHelper {
    private const val TAG = "EchoCancellationHelper"

    fun attachAEC(audioSessionId: Int): AcousticEchoCanceler? {
        if (!AcousticEchoCanceler.isAvailable()) {
            Log.w(TAG, "Hardware AcousticEchoCanceler is NOT available on this device.")
            return null
        }

        return try {
            val aec = AcousticEchoCanceler.create(audioSessionId)
            if (aec != null) {
                aec.enabled = true
                Log.i(TAG, "Hardware AcousticEchoCanceler enabled successfully for session $audioSessionId.")
            } else {
                Log.w(TAG, "Failed to create AcousticEchoCanceler instance.")
            }
            aec
        } catch (e: Exception) {
            Log.e(TAG, "Exception while enabling AcousticEchoCanceler: ${e.message}", e)
            null
        }
    }
}
