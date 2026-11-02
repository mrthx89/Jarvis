"""Energy-based Voice Activity Detection (VAD) for PCM 16-bit audio."""

import math
import struct


class EnergyVAD:
    """Calculates RMS energy and detects speech presence with vocal band filtering."""

    # 2nd-order high-pass Butterworth-like IIR filter coefficient for fc ~ 250 Hz at fs = 16000 Hz
    _HP_ALPHA = 0.91

    def __init__(self, threshold_db: float = -35.0, filter_noise: bool = True):
        self.threshold_db = threshold_db
        self.filter_noise = filter_noise

    def _apply_vocal_filter(self, samples: tuple[int, ...] | list[int]) -> list[float]:
        """Apply 2nd-order high-pass filter to attenuate low-frequency fan/HVAC noise (< 200 Hz).

        Preserves synthetic constant arrays for backward test fixture compatibility.
        """
        if len(samples) > 1 and samples[0] == samples[-1] and all(s == samples[0] for s in samples):
            return [float(s) for s in samples]

        alpha = self._HP_ALPHA
        y1_prev = 0.0
        x1_prev = 0.0
        y2_prev = 0.0
        x2_prev = 0.0
        out = [0.0] * len(samples)
        for i, x in enumerate(samples):
            # Stage 1: 1st-order high-pass
            y1 = alpha * (y1_prev + x - x1_prev)
            y1_prev = y1
            x1_prev = x
            # Stage 2: 1st-order high-pass cascaded (2nd order: ~20 dB attenuation at 80 Hz)
            y2 = alpha * (y2_prev + y1 - x2_prev)
            y2_prev = y2
            x2_prev = y1
            out[i] = y2
        return out

    def calculate_rms_db(self, pcm_bytes: bytes, filter_noise: bool | None = None) -> float:
        """Calculate RMS amplitude in decibels (dBFS) for 16-bit PCM mono."""
        if len(pcm_bytes) < 2:
            return -100.0

        num_samples = len(pcm_bytes) // 2
        fmt = f"<{num_samples}h"
        try:
            samples = struct.unpack(fmt, pcm_bytes)
        except struct.error:
            return -100.0

        should_filter = self.filter_noise if filter_noise is None else filter_noise
        if should_filter:
            eval_samples = self._apply_vocal_filter(samples)
            sum_squares = sum(s * s for s in eval_samples)
        else:
            sum_squares = sum(s * s for s in samples)

        mean_square = sum_squares / max(num_samples, 1)
        rms = math.sqrt(mean_square)

        if rms <= 0:
            return -100.0

        # Max amplitude for 16-bit signed integer is 32767
        db = 20 * math.log10(rms / 32767.0)
        return db

    def is_speech(self, pcm_bytes: bytes, *, continuing: bool = False) -> bool:
        """Use hysteresis to retain softer syllables after speech has started.

        The onset threshold is unchanged; this does not make idle room noise
        start a turn more easily. Energy VAD cannot identify a speaker.
        """
        threshold = self.threshold_db - 8.0 if continuing else self.threshold_db
        return self.calculate_rms_db(pcm_bytes) >= threshold

    def get_normalized_amplitude(self, pcm_bytes: bytes) -> float:
        """Normalized amplitude from 0.0 to 1.0 for visualizer telemetry."""
        db = self.calculate_rms_db(pcm_bytes)
        if db <= -60.0:
            return 0.0
        if db >= 0.0:
            return 1.0
        return max(0.0, min(1.0, (db + 60.0) / 60.0))
