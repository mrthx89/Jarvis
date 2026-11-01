"""Unit tests for models and VAD engine using standard library unittest."""

import unittest
from nexo_server.models.messages import (
    ClientHello,
    ServerHello,
    StateChange,
    VisualTelemetry,
    TranscriptionMessage,
    NexoState,
    JarvisState,
)
from nexo_server.pipeline.vad import EnergyVAD


class TestNexoServer(unittest.TestCase):
    def test_models_serialization(self):
        hello = ClientHello(device_id="test_tablet_01")
        serialized = hello.model_dump_json()
        self.assertIn("test_tablet_01", serialized)
        self.assertIn("client_hello", serialized)

        server_hello = ServerHello(session_id="sess_01")
        self.assertEqual(server_hello.agent_name, "Nexo")
        self.assertEqual(server_hello.status, NexoState.IDLE)

        state = StateChange(state=NexoState.LISTENING)
        self.assertEqual(state.state, "LISTENING")

        # Test dual-compatibility alias
        state_jarvis = StateChange(state=JarvisState.LISTENING)
        self.assertEqual(state_jarvis.state, "LISTENING")
        self.assertIs(JarvisState, NexoState)

        telemetry = VisualTelemetry(amplitude=0.85, frequency_bands=[0.1, 0.5, 0.9])
        self.assertEqual(telemetry.amplitude, 0.85)
        self.assertEqual(len(telemetry.frequency_bands), 3)

    def test_dual_compatibility_imports(self):
        import jarvis_server.models.messages as legacy_msgs
        import jarvis_server.pipeline.vad as legacy_vad
        self.assertTrue(issubclass(legacy_msgs.ClientHello, object))
        self.assertIsNotNone(legacy_msgs.JarvisState)
        self.assertIsNotNone(legacy_vad.EnergyVAD)

    def test_energy_vad_silence_and_signal(self):
        vad = EnergyVAD(threshold_db=-30.0)

        # Pure silence (all zeros)
        silence = b"\x00\x00" * 1600
        self.assertLessEqual(vad.calculate_rms_db(silence), -90.0)
        self.assertFalse(vad.is_speech(silence))
        self.assertEqual(vad.get_normalized_amplitude(silence), 0.0)

        # Strong simulated signal
        loud_signal = b"\x7f\x7f\x80\x80" * 800
        self.assertGreater(vad.calculate_rms_db(loud_signal), -30.0)
        self.assertTrue(vad.is_speech(loud_signal))
        self.assertGreater(vad.get_normalized_amplitude(loud_signal), 0.5)


if __name__ == "__main__":
    unittest.main()
