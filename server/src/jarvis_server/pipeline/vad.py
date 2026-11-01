"""Energy-based Voice Activity Detection (VAD) for PCM 16-bit audio."""

import math
import struct


class EnergyVAD:
    """Calculates RMS energy and detects speech presence."""

    def __init__(self, threshold_db: float = -35.0):
        self.threshold_db = threshold_db

    def calculate_rms_db(self, pcm_bytes: bytes) -> float:
        """Calculate RMS amplitude in decibels (dBFS) for 16-bit PCM mono."""
        if len(pcm_bytes) < 2:
            return -100.0

        num_samples = len(pcm_bytes) // 2
        fmt = f"<{num_samples}h"
        try:
            samples = struct.unpack(fmt, pcm_bytes)
        except struct.error:
            return -100.0

        sum_squares = sum(s * s for s in samples)
        mean_square = sum_squares / max(num_samples, 1)
        rms = math.sqrt(mean_square)

        if rms <= 0:
            return -100.0

        # Max amplitude for 16-bit signed integer is 32767
        db = 20 * math.log10(rms / 32767.0)
        return db

    def is_speech(self, pcm_bytes: bytes) -> bool:
        """Determine if audio chunk contains speech based on energy threshold."""
        return self.calculate_rms_db(pcm_bytes) >= self.threshold_db

    def get_normalized_amplitude(self, pcm_bytes: bytes) -> float:
        """Normalized amplitude from 0.0 to 1.0 for visualizer telemetry."""
        db = self.calculate_rms_db(pcm_bytes)
        if db <= -60.0:
            return 0.0
        if db >= 0.0:
            return 1.0
        return max(0.0, min(1.0, (db + 60.0) / 60.0))
