"""Unit tests for models and VAD engine using standard library unittest."""

import unittest
from jarvis_server.models.messages import (
    ClientHello,
    ServerHello,
    StateChange,
    VisualTelemetry,
    TranscriptionMessage,
    JarvisState,
)
from jarvis_server.pipeline.vad import EnergyVAD


class TestJarvisServer(unittest.TestCase):
    def test_models_serialization(self):
        hello = ClientHello(device_id="test_tablet_01")
        serialized = hello.model_dump_json()
        self.assertIn("test_tablet_01", serialized)
        self.assertIn("client_hello", serialized)

        state = StateChange(state=JarvisState.LISTENING)
        self.assertEqual(state.state, "LISTENING")

        telemetry = VisualTelemetry(amplitude=0.85, frequency_bands=[0.1, 0.5, 0.9])
        self.assertEqual(telemetry.amplitude, 0.85)
        self.assertEqual(len(telemetry.frequency_bands), 3)

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
