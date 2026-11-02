"""Low-frequency ambient fan and HVAC noise suppression for vocal VAD."""
import math
import struct
import pytest

from nexo_server.pipeline.vad import EnergyVAD


def generate_tone(freq_hz: float, amp: int, num_samples: int = 1600, rate: int = 16000) -> bytes:
    samples = [int(amp * math.sin(2 * math.pi * freq_hz * i / rate)) for i in range(num_samples)]
    return struct.pack(f"<{num_samples}h", *samples)


def test_fan_rumble_below_200hz_is_attenuated_and_does_not_trigger_onset():
    """80 Hz fan rumble with -31.7 dBFS raw amplitude must not trigger speech onset."""
    vad = EnergyVAD(threshold_db=-35.0)
    # Amplitude 1200 gives raw RMS ~848 (-31.7 dBFS), which would trigger unfiltered VAD
    fan_noise = generate_tone(freq_hz=80.0, amp=1200)
    assert not vad.is_speech(fan_noise)
    assert not vad.is_speech(fan_noise, continuing=False)


def test_vocal_frequency_speech_triggers_onset_normally():
    """800 Hz human voice formant with identical amplitude triggers speech onset."""
    vad = EnergyVAD(threshold_db=-35.0)
    vocal_speech = generate_tone(freq_hz=800.0, amp=1200)
    assert vad.is_speech(vocal_speech)


def test_hvac_60hz_hum_is_rejected_even_during_continuation():
    """60 Hz electrical/HVAC hum remains rejected below continuation threshold."""
    vad = EnergyVAD(threshold_db=-35.0)
    hvac_hum = generate_tone(freq_hz=60.0, amp=1200)
    assert not vad.is_speech(hvac_hum, continuing=True)
