package id.my.yhdev.nexo.audio

import android.content.Context
import android.media.AudioAttributes
import android.media.SoundPool
import android.util.Log
import id.my.yhdev.nexo.R

/**
 * Manages soft audio chimes and earcons for interactive voice states (LISTENING & THINKING).
 * Uses low-latency SoundPool with USAGE_ASSISTANCE_SONIFICATION / USAGE_ASSISTANT.
 */
class EarconManager(private val context: Context) {
    private val soundPool: SoundPool? = try {
        val attrs = AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_ASSISTANCE_SONIFICATION)
            .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
            .build()
        SoundPool.Builder()
            .setMaxStreams(2)
            .setAudioAttributes(attrs)
            .build()
    } catch (e: Exception) {
        Log.w(TAG, "SoundPool creation failed: ${e.message}")
        null
    }

    private var listeningChimeId: Int = 0
    private var thinkingChimeId: Int = 0
    private var listeningLoaded = false
    private var thinkingLoaded = false

    init {
        soundPool?.let { pool ->
            pool.setOnLoadCompleteListener { _, sampleId, status ->
                if (status == 0) {
                    if (sampleId == listeningChimeId) listeningLoaded = true
                    if (sampleId == thinkingChimeId) thinkingLoaded = true
                }
            }
            try {
                listeningChimeId = pool.load(context, R.raw.chime_listening, 1)
                thinkingChimeId = pool.load(context, R.raw.chime_thinking, 1)
            } catch (e: Exception) {
                Log.w(TAG, "Failed loading earcon chimes: ${e.message}")
            }
        }
    }

    fun playListening() {
        if (listeningLoaded && soundPool != null) {
            soundPool.play(listeningChimeId, 0.65f, 0.65f, 1, 0, 1.0f)
        }
    }

    fun playThinking() {
        if (thinkingLoaded && soundPool != null) {
            soundPool.play(thinkingChimeId, 0.55f, 0.55f, 1, 0, 1.0f)
        }
    }

    fun release() {
        try {
            soundPool?.release()
        } catch (e: Exception) {
            Log.w(TAG, "Error releasing SoundPool: ${e.message}")
        }
    }

    companion object {
        private const val TAG = "EarconManager"
    }
}
