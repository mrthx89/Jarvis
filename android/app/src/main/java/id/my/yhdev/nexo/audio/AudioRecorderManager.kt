package id.my.yhdev.nexo.audio

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.AudioRouting
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.NoiseSuppressor
import android.os.Handler
import android.os.Looper
import id.my.yhdev.nexo.audio.core.CapturePump
import id.my.yhdev.nexo.audio.core.GenerationGate

class AudioRecorderManager(
    private val sampleRate: Int = 16000,
    private val onError: (String) -> Unit = {},
    private val onRecoverableError: (String) -> Unit = onError,
    private val options: CaptureOptions = CaptureOptions.legacyComparison(),
    private val audioManager: AudioManager? = null,
    private val onDiagnostics: (CaptureDiagnostics) -> Unit = {},
    private val onRouteInvalidated: () -> Unit = {},
    private val onChunkCaptured: (ByteArray, Int) -> Unit
) {
    private val generation = GenerationGate()
    @Volatile private var captureToken = 0L
    private var pump: CapturePump? = null
    @Volatile private var active = false
    @Volatile private var finishing = false
    @Volatile var diagnostics: CaptureDiagnostics? = null
        private set

    /** Lifecycle worker only, never UI/read thread. Returns true after actual hardware startup. */
    @SuppressLint("MissingPermission")
    @Synchronized
    fun startRecording(): Boolean {
        if (active) return true
        if (!stopAndJoin()) { onError("recorder_stop_timeout"); return false }
        val token = generation.begin()
        captureToken = token
        var record: AudioRecord? = null
        var aec: AcousticEchoCanceler? = null
        var ns: NoiseSuppressor? = null
        try {
            require(sampleRate == 16000) { "invalid_format" }
            val minBuffer = AudioRecord.getMinBufferSize(sampleRate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
            require(minBuffer > 0) { "invalid_format" }
            val unprocessed = audioManager?.getProperty(AudioManager.PROPERTY_SUPPORT_AUDIO_SOURCE_UNPROCESSED) == "true"
            val fallback = options.source == CaptureSource.UNPROCESSED && !unprocessed
            val selected = if (fallback) CaptureSource.VOICE_RECOGNITION else options.source
            val current = AudioRecord(selected.value, sampleRate, AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT, maxOf(minBuffer, 6400))
            record = current
            check(current.state == AudioRecord.STATE_INITIALIZED) { "recorder_init_failed" }
            require(current.sampleRate == sampleRate && current.channelCount == 1 &&
                current.audioFormat == AudioFormat.ENCODING_PCM_16BIT) { "invalid_format" }
            // Effects are requested only for the explicitly selected comparison path, never stacked with software NS.
            if (options.requestAec) aec = EchoCancellationHelper.attachAEC(current.audioSessionId)
            if (options.requestNs) ns = EchoCancellationHelper.attachNoiseSuppressor(current.audioSessionId)
            current.startRecording()
            check(current.recordingState == AudioRecord.RECORDSTATE_RECORDING) { "recorder_start_failed" }
            val route = current.routedDevice
            diagnostics = CaptureDiagnostics(options.source, selected,
                if (fallback) "unprocessed_not_advertised" else null, current.sampleRate, current.channelCount,
                current.audioFormat, route?.type, route?.id, AcousticEchoCanceler.isAvailable(), options.requestAec,
                aec?.enabled == true, NoiseSuppressor.isAvailable(), options.requestNs, ns?.enabled == true)
            val routeListener = AudioRouting.OnRoutingChangedListener { routing ->
                if (generation.matches(token) && routing.routedDevice?.id != route?.id) {
                    // Fence immediately. Caller schedules physical stop on its lifecycle worker.
                    generation.invalidate(token); active = false
                    onRouteInvalidated()
                    onError("route_changed")
                }
            }
            current.addOnRoutingChangedListener(routeListener, Handler(Looper.getMainLooper()))
            val sessionAec = aec
            val sessionNs = ns
            val source = object : CapturePump.Source {
                override fun read(target: ByteArray): Int = current.read(target, 0, target.size)
                override fun stop() { runCatching { current.stop() } }
                override fun release() {
                    current.removeOnRoutingChangedListener(routeListener)
                    runCatching { sessionAec?.release() }
                    runCatching { sessionNs?.release() }
                    runCatching { current.release() }
                }
            }
            finishing = false
            val session = CapturePump(source, { bytes, count ->
                if (generation.matches(token)) onChunkCaptured(bytes, count)
            }, { code ->
                if (generation.matches(token)) {
                    active = false; generation.invalidate(token)
                    if (code == "permission_loss") onError(code) else onRecoverableError(code)
                }
            }, 32, 3200, if (options.automaticVad) 8192 else 51200)
            pump = session
            if (!generation.activate(token)) {
                source.stop(); source.release(); pump = null
                return false
            }
            active = true
            session.start()
            if (!generation.isActive(token)) { stopAndJoin(); return false }
            diagnostics?.let(onDiagnostics)
            return isRecording()
        } catch (_: SecurityException) {
            onError("permission_loss")
        } catch (_: Exception) {
            onRecoverableError("recorder_start_failed")
        }
        active = false
        generation.invalidate(captureToken)
        runCatching { aec?.release() }; runCatching { ns?.release() }
        runCatching { record?.stop() }; runCatching { record?.release() }
        return false
    }

    /** Finish drains already captured slots. Only seal LocalCapture after true. */
    @Synchronized
    fun finishAndJoin(timeoutMs: Long = 1500): Boolean {
        finishing = true
        val done = pump?.finish(timeoutMs) ?: true
        active = false
        if (done) { pump = null; generation.invalidate(captureToken) }
        else { generation.invalidate(captureToken); onError("recorder_stop_timeout") }
        return done
    }

    /** Cancel fences before touching hardware; false forbids starting another AudioRecord. */
    @Synchronized
    fun stopAndJoin(timeoutMs: Long = 1500): Boolean {
        generation.invalidate(captureToken); active = false; finishing = false
        val done = pump?.stop(timeoutMs) ?: true
        if (done) pump = null
        return done
    }

    fun stopRecording() { if (!stopAndJoin()) onError("recorder_stop_timeout") }
    fun isRecording(): Boolean = active && generation.isActive(captureToken) && !finishing && pump?.isRunning == true
    fun queueHighWater(): Int = pump?.highWater() ?: 0
}
