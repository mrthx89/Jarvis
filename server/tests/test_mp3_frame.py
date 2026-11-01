"""Tests for lightweight MP3 frame header parsing and duration calculation."""

import io
import math
import av
import numpy as np
import pytest

from nexo_server.gateway.mp3_frame import (
    parse_mp3_header,
    parse_mp3_duration,
    parse_audio_duration,
    skip_id3,
    MPEG_1,
    MPEG_2,
    MPEG_25,
    LAYER_III,
)


def generate_mp3_tone(sample_rate: int = 24000, duration_seconds: float = 1.0) -> bytes:
    """Generate synthesized MP3 audio using av for ground-truth verification."""
    output = io.BytesIO()
    total_samples = int(sample_rate * duration_seconds)
    with av.open(output, mode="w", format="mp3") as container:
        stream = container.add_stream("mp3", rate=sample_rate)
        stream.layout = "mono"
        t = np.arange(total_samples) / float(sample_rate)
        samples = (0.4 * np.sin(2 * math.pi * 440 * t)).astype("float32")
        frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="fltp", layout="mono")
        frame.sample_rate = sample_rate
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return output.getvalue()


def test_parse_mp3_header_valid():
    # Construct a valid MPEG-1 Layer III 128kbps 44100Hz header
    # b0: 0xFF
    # b1: 111 (sync) + 11 (MPEG1) + 01 (Layer III) + 1 (no CRC) = 0xFB
    # b2: 1001 (128kbps) + 00 (44100Hz) + 0 (no pad) + 0 = 0x90
    # b3: 0x00
    header = bytes([0xFF, 0xFB, 0x90, 0x00])
    res = parse_mp3_header(header)
    assert res is not None
    frame_size, duration, sample_rate = res
    assert sample_rate == 44100
    # For MPEG1 Layer III: frame_size = 144 * 128000 / 44100 = 417
    assert frame_size == 417
    # Duration: 1152 / 44100 ~= 0.026122 s
    assert abs(duration - (1152.0 / 44100.0)) < 1e-6


def test_parse_mp3_duration_on_real_audio():
    # Generate 1.0s of 24kHz MP3
    mp3_data = generate_mp3_tone(sample_rate=24000, duration_seconds=1.0)
    assert len(mp3_data) > 0

    duration = parse_mp3_duration(mp3_data)
    # The actual encoded MP3 duration is ~1.0 second (within +/- 50ms due to encoder framing)
    assert 0.95 <= duration <= 1.10

    # Also test 0.5s audio
    mp3_half = generate_mp3_tone(sample_rate=24000, duration_seconds=0.5)
    dur_half = parse_mp3_duration(mp3_half)
    assert 0.45 <= dur_half <= 0.60


def test_parse_audio_duration_mp3_vs_pcm():
    # 1. MP3 payload
    mp3_data = generate_mp3_tone(sample_rate=24000, duration_seconds=1.0)
    dur_mp3 = parse_audio_duration(mp3_data)
    assert 0.95 <= dur_mp3 <= 1.10

    # 2. Raw PCM payload (24kHz 16-bit mono = 48000 bytes/sec)
    # 48000 bytes = 1.0 second of PCM
    pcm_data = bytes(48000)
    dur_pcm = parse_audio_duration(pcm_data, default_sample_rate=24000)
    assert dur_pcm == 1.0

    # 960 bytes = 20ms of PCM
    dur_pcm_frame = parse_audio_duration(bytes(960), default_sample_rate=24000)
    assert dur_pcm_frame == 0.02


def test_skip_id3():
    # Construct dummy ID3 header
    # ID3 + ver (2) + flags (1) + synchsafe size 10 (4 bytes: 0, 0, 0, 10)
    id3 = b"ID3\x03\x00\x00\x00\x00\x00\x0a" + (b"\x00" * 10) + b"AUDIO_DATA"
    payload = skip_id3(id3)
    assert payload == b"AUDIO_DATA"


def test_parse_mp3_duration_empty_or_invalid():
    assert parse_mp3_duration(b"") == 0.0
    assert parse_mp3_duration(b"XYZ") == 0.0
    assert parse_mp3_duration(b"Random string without mp3 sync") == 0.0


def test_parse_mp3_header_mpeg_versions_and_layers():
    # 1. MPEG-2 Layer III 64kbps 24000Hz (no pad)
    # b0: 0xFF
    # b1: 111 (sync) + 10 (MPEG2) + 01 (Layer III) + 1 (no CRC) = 0xF3
    # b2: 1000 (64kbps) + 01 (24000Hz) + 0 (no pad) + 0 = 0x84
    # b3: 0x00
    h_mpeg2 = bytes([0xFF, 0xF3, 0x84, 0x00])
    res = parse_mp3_header(h_mpeg2)
    assert res is not None
    frame_size, duration, sample_rate = res
    assert sample_rate == 24000
    # For MPEG-2 Layer III: frame_size = 72 * 64000 / 24000 = 192
    assert frame_size == 192
    # Samples per frame for MPEG-2 Layer III: 576. 576 / 24000 = 0.024s
    assert abs(duration - (576.0 / 24000.0)) < 1e-6

    # 2. MPEG-2.5 Layer III 32kbps 12000Hz (no pad)
    # b0: 0xFF
    # b1: 111 (sync) + 00 (MPEG2.5) + 01 (Layer III) + 1 (no CRC) = 0xE3
    # b2: 0100 (32kbps) + 01 (12000Hz) + 0 (no pad) + 0 = 0x44
    # b3: 0x00
    h_mpeg25 = bytes([0xFF, 0xE3, 0x44, 0x00])
    res25 = parse_mp3_header(h_mpeg25)
    assert res25 is not None
    frame_size25, duration25, sample_rate25 = res25
    assert sample_rate25 == 12000
    # For MPEG-2.5 Layer III: frame_size = 72 * 32000 / 12000 = 192
    assert frame_size25 == 192
    assert abs(duration25 - (576.0 / 12000.0)) < 1e-6


def test_parse_mp3_header_padding_bit():
    # MPEG-1 Layer III 128kbps 44100Hz without padding:
    # 144 * 128000 / 44100 = 417 bytes
    h_no_pad = bytes([0xFF, 0xFB, 0x90, 0x00])
    # With padding bit set (b2 bit 1):
    h_pad = bytes([0xFF, 0xFB, 0x92, 0x00])

    res_no_pad = parse_mp3_header(h_no_pad)
    res_pad = parse_mp3_header(h_pad)
    assert res_no_pad is not None
    assert res_pad is not None
    size_no_pad = res_no_pad[0]
    size_pad = res_pad[0]
    assert size_pad == size_no_pad + 1


def test_parse_mp3_header_invalid_bitrate_and_sample_rate():
    # Bitrate index 0 (free) -> b2 high nibble = 0
    assert parse_mp3_header(bytes([0xFF, 0xFB, 0x00, 0x00])) is None
    # Bitrate index 15 (bad) -> b2 high nibble = 0xF
    assert parse_mp3_header(bytes([0xFF, 0xFB, 0xF0, 0x00])) is None
    # Sample rate index 3 (reserved) -> b2 bits 3-2 = 11 (0x0C)
    assert parse_mp3_header(bytes([0xFF, 0xFB, 0x9C, 0x00])) is None
    # Version reserved (1) -> b1 bits 4-3 = 01 (0x08)
    assert parse_mp3_header(bytes([0xFF, 0xE9, 0x90, 0x00])) is None
    # Layer reserved (0) -> b1 bits 2-1 = 00 (0x00)
    assert parse_mp3_header(bytes([0xFF, 0xF9, 0x90, 0x00])) is None


def test_skip_id3_synchsafe_and_extended_headers():
    # Synchsafe integer size 256: 0x00, 0x00, 0x02, 0x00 -> (2 << 7) = 256
    id3 = b"ID3\x04\x00\x00\x00\x00\x02\x00" + (b"\xAA" * 256) + b"PAYLOAD_START"
    assert skip_id3(id3) == b"PAYLOAD_START"

    # Too short ID3 header (<10 bytes) should not crash or slice out of bounds
    assert skip_id3(b"ID3\x03\x00") == b"ID3\x03\x00"


def test_parse_audio_duration_pcm_various_sample_rates():
    # 16000 Hz 16-bit mono = 32000 bytes/sec
    pcm_16k = bytes(32000)
    assert parse_audio_duration(pcm_16k, default_sample_rate=16000) == 1.0

    # 48000 Hz 16-bit mono = 96000 bytes/sec
    pcm_48k = bytes(48000)
    assert parse_audio_duration(pcm_48k, default_sample_rate=48000) == 0.5


def test_mp3_frame_dual_compatibility():
    import jarvis_server.gateway.mp3_frame as jarvis_mp3
    import nexo_server.gateway.mp3_frame as nexo_mp3

    header = bytes([0xFF, 0xFB, 0x90, 0x00])
    assert jarvis_mp3.parse_mp3_header(header) == nexo_mp3.parse_mp3_header(header)
    assert jarvis_mp3.parse_audio_duration(bytes(48000), 24000) == nexo_mp3.parse_audio_duration(bytes(48000), 24000)

