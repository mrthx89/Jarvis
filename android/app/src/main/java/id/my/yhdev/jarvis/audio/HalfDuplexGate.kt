package id.my.yhdev.jarvis.audio

import id.my.yhdev.jarvis.ui.JarvisState

/** Half duplex only: microphone frames are discarded while the server works or audio drains. */
class HalfDuplexGate {
    @Volatile private var ready = false
    @Volatile private var microphone = false
    @Volatile private var state = JarvisState.DISCONNECTED
    @Volatile var playbackPending = false
        private set

    val canSendAudio: Boolean
        get() = ready && microphone && !playbackPending &&
            (state == JarvisState.IDLE || state == JarvisState.LISTENING)

    fun connected() { ready = true; microphone = false; state = JarvisState.IDLE }
    fun disconnected() { ready = false; microphone = false; playbackPending = false; state = JarvisState.DISCONNECTED }
    fun microphoneReady(ready: Boolean) { microphone = ready }
    fun stateChanged(state: JarvisState) { this.state = state }
    fun ttsStarted() { playbackPending = true }
    fun playbackDrained() { playbackPending = false }

    /** A failed output ends the turn, but only a new handshake may re-enable capture. */
    fun playbackFailed() { disconnected() }
}
