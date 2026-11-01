package id.my.yhdev.nexo.audio

import id.my.yhdev.nexo.ui.NexoState

/** Half duplex only: microphone frames are discarded while the server works or audio drains. */
class HalfDuplexGate {
    @Volatile private var ready = false
    @Volatile private var microphone = false
    @Volatile private var state = NexoState.DISCONNECTED
    @Volatile var playbackPending = false
        private set

    val canSendAudio: Boolean
        get() = ready && microphone && !playbackPending &&
            (state == NexoState.IDLE || state == NexoState.LISTENING)

    fun connected() { ready = true; microphone = false; state = NexoState.IDLE }
    fun disconnected() { ready = false; microphone = false; playbackPending = false; state = NexoState.DISCONNECTED }
    fun microphoneReady(ready: Boolean) { microphone = ready }
    fun stateChanged(state: NexoState) { this.state = state }
    fun ttsStarted() { playbackPending = true }
    fun playbackDrained() { playbackPending = false }

    /** A failed output ends the turn, but only a new handshake may re-enable capture. */
    fun playbackFailed() { disconnected() }
}
