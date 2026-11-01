"""Hysteresis retains quiet speech without lowering idle onset sensitivity."""
import struct

from nexo_server.pipeline.vad import EnergyVAD


def test_quiet_sound_is_not_onset_but_can_continue_a_phrase():
    vad = EnergyVAD()
    quiet = struct.pack('<h', 328) * 1600
    assert not vad.is_speech(quiet)
    assert vad.is_speech(quiet, continuing=True)


def test_real_silence_still_ends_a_phrase():
    vad = EnergyVAD()
    assert not vad.is_speech(bytes(3200), continuing=True)
    assert not vad.is_speech(b'\x01\x00' * 1600, continuing=True)
