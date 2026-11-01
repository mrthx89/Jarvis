package id.my.yhdev.nexo.audio

import id.my.yhdev.nexo.ui.NexoState
import org.junit.Assert.*
import org.junit.Test

class HalfDuplexGateTest {
    @Test fun doesNotSendUntilHandshakeAndMicrophoneReady() {
        val gate = HalfDuplexGate()
        assertFalse(gate.canSendAudio)
        gate.connected()
        assertFalse(gate.canSendAudio)
        gate.microphoneReady(true)
        assertTrue(gate.canSendAudio)
        gate.disconnected()
        assertFalse(gate.canSendAudio)
    }

    @Test fun processingAndPlaybackPauseUntilBothIdleAndDrain() {
        val gate = HalfDuplexGate()
        gate.connected()
        gate.microphoneReady(true)
        gate.stateChanged(NexoState.THINKING)
        assertFalse(gate.canSendAudio)
        gate.ttsStarted()
        gate.stateChanged(NexoState.IDLE)
        assertFalse("Server idle must not cut off queued audio", gate.canSendAudio)
        gate.playbackDrained()
        assertTrue(gate.canSendAudio)
        gate.stateChanged(NexoState.ERROR)
        assertFalse(gate.canSendAudio)
    }

    @Test fun playbackFailureClearsPendingButRequiresFreshHandshakeAndMicrophone() {
        val gate = HalfDuplexGate()
        gate.connected()
        gate.microphoneReady(true)
        gate.ttsStarted()
        gate.stateChanged(NexoState.IDLE)

        gate.playbackFailed()

        assertFalse("Failure must terminate the pending playback", gate.playbackPending)
        assertFalse("Failure must not claim an active microphone", gate.canSendAudio)
        gate.playbackDrained()
        gate.stateChanged(NexoState.LISTENING)
        gate.microphoneReady(true)
        assertFalse("Late idle/drain events cannot recover a failed connection", gate.canSendAudio)
        gate.connected()
        assertFalse("Reconnect still requires microphone startup", gate.canSendAudio)
        gate.microphoneReady(true)
        assertTrue(gate.canSendAudio)
    }

    @Test fun drainBeforeIdleStillWaitsForServer() {
        val gate = HalfDuplexGate()
        gate.connected()
        gate.microphoneReady(true)
        gate.stateChanged(NexoState.SPEAKING)
        gate.ttsStarted()
        gate.playbackDrained()
        assertFalse(gate.canSendAudio)
        gate.stateChanged(NexoState.LISTENING)
        assertTrue(gate.canSendAudio)
        gate.microphoneReady(false)
        assertFalse(gate.canSendAudio)
    }
}
