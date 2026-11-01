package id.my.yhdev.nexo.audio

import id.my.yhdev.nexo.ui.NexoState

/** Half duplex only: microphone frames are discarded while the server works or audio drains. */
class HalfDuplexGate {
    @Volatile private var ready = false
    @Volatile private var microphone = false
    @Volatile private var state = NexoState.DISCONNECTED
    @Volatile var playbackPending = false
        private set

    // Individual volatile fields are not an atomic snapshot. The capture thread
    // must observe a complete transition, not half of connected()/disconnected().
    val canSendAudio: Boolean
        @Synchronized get() = ready && microphone && !playbackPending &&
            (state == NexoState.IDLE || state == NexoState.LISTENING)

    @Synchronized fun connected() {
        if (ready) return // Duplicate acknowledgement must not reopen a busy turn.
        microphone = false
        state = NexoState.IDLE
        playbackPending = false
        ready = true
    }
    @Synchronized fun disconnected() { ready = false; microphone = false; playbackPending = false; state = NexoState.DISCONNECTED }
    @Synchronized fun microphoneReady(ready: Boolean) { microphone = ready }
    @Synchronized fun stateChanged(state: NexoState) { this.state = state }
    @Synchronized fun ttsStarted() { playbackPending = true }
    @Synchronized fun playbackDrained() { playbackPending = false }

    /** A failed output ends the turn, but only a new handshake may re-enable capture. */
    @Synchronized fun playbackFailed() { disconnected() }
}
