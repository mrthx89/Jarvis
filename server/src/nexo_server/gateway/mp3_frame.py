"""Lightweight MP3 frame header parsing and duration calculation.

Calculates exact audio playback duration by inspecting MPEG audio frame headers
without invoking heavy external decoders or subprocesses.
"""

from typing import Optional, Tuple

# MPEG Audio version ID (bits 20-19)
MPEG_25 = 0  # MPEG Version 2.5
MPEG_RESERVED = 1
MPEG_2 = 2   # MPEG Version 2 (ISO/IEC 13818-3)
MPEG_1 = 3   # MPEG Version 1 (ISO/IEC 11172-3)

# Layer description (bits 18-17)
LAYER_RESERVED = 0
LAYER_III = 1  # Layer 3 (MP3)
LAYER_II = 2   # Layer 2
LAYER_I = 3    # Layer 1

# Bitrate lookup table in kbps: (version, layer) -> [16 bitrates]
# Index 0 is free, index 15 is bad.
BITRATES = {
    # Layer I
    (MPEG_1, LAYER_I): [0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448, 0],
    (MPEG_2, LAYER_I): [0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256, 0],
    (MPEG_25, LAYER_I): [0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256, 0],
    # Layer II
    (MPEG_1, LAYER_II): [0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384, 0],
    (MPEG_2, LAYER_II): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0],
    (MPEG_25, LAYER_II): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0],
    # Layer III (MP3)
    (MPEG_1, LAYER_III): [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0],
    (MPEG_2, LAYER_III): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0],
    (MPEG_25, LAYER_III): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0],
}

# Sample rate lookup table in Hz: version -> [3 rates]
SAMPLE_RATES = {
    MPEG_1: [44100, 48000, 32000],
    MPEG_2: [22050, 24000, 16000],
    MPEG_25: [11025, 12000, 8000],
}

# Samples per frame: (version, layer) or layer -> int
SAMPLES_PER_FRAME = {
    LAYER_I: 384,
    LAYER_II: 1152,
    (MPEG_1, LAYER_III): 1152,
    (MPEG_2, LAYER_III): 576,
    (MPEG_25, LAYER_III): 576,
}


def parse_mp3_header(header_bytes: bytes) -> Optional[Tuple[int, float, int]]:
    """Parse 4-byte MPEG audio frame header.

    Returns (frame_size_bytes, frame_duration_seconds, sample_rate_hz) or None.
    """
    if len(header_bytes) < 4:
        return None

    b0, b1, b2, b3 = header_bytes[:4]
    # Sync: 11 bits set (0xFF and top 3 bits of b1 == 0xE0)
    if b0 != 0xFF or (b1 & 0xE0) != 0xE0:
        return None

    version_id = (b1 >> 3) & 0x03
    if version_id == MPEG_RESERVED:
        return None

    layer_id = (b1 >> 1) & 0x03
    if layer_id == LAYER_RESERVED:
        return None

    bitrate_idx = (b2 >> 4) & 0x0F
    if bitrate_idx == 0 or bitrate_idx == 15:
        return None

    samplerate_idx = (b2 >> 2) & 0x03
    if samplerate_idx == 3:  # Reserved
        return None

    padding = (b2 >> 1) & 0x01

    rates = SAMPLE_RATES.get(version_id)
    if not rates or samplerate_idx >= len(rates):
        return None
    sample_rate = rates[samplerate_idx]

    bitrate_table = BITRATES.get((version_id, layer_id))
    if not bitrate_table or bitrate_idx >= len(bitrate_table):
        return None
    bitrate_kbps = bitrate_table[bitrate_idx]
    if bitrate_kbps == 0:
        return None
    bitrate_bps = bitrate_kbps * 1000

    if layer_id == LAYER_I:
        samples = SAMPLES_PER_FRAME[LAYER_I]
        frame_size = int((12 * bitrate_bps / sample_rate) + padding * 4)
    elif layer_id == LAYER_II:
        samples = SAMPLES_PER_FRAME[LAYER_II]
        frame_size = int((144 * bitrate_bps / sample_rate) + padding)
    else:  # LAYER_III
        samples = SAMPLES_PER_FRAME.get((version_id, LAYER_III), 1152)
        if version_id == MPEG_1:
            frame_size = int((144 * bitrate_bps / sample_rate) + padding)
        else:
            frame_size = int((72 * bitrate_bps / sample_rate) + padding)

    if frame_size < 4:
        return None

    duration = samples / float(sample_rate)
    return frame_size, duration, sample_rate


def skip_id3(data: bytes) -> bytes:
    """Skip ID3v2 tag at the start of audio data if present."""
    if data.startswith(b"ID3") and len(data) >= 10:
        tag_size = 10 + sum((data[6 + i] & 0x7F) << (21 - 7 * i) for i in range(4))
        if tag_size <= len(data):
            return data[tag_size:]
    return data


def parse_mp3_duration(data: bytes) -> float:
    """Parse total playback duration (in seconds) of MP3 frames in data.

    Returns 0.0 if data does not contain valid MP3 frames.
    """
    if not data or len(data) < 4:
        return 0.0

    payload = skip_id3(data)
    total_duration = 0.0
    idx = 0
    length = len(payload)
    valid_frames = 0

    while idx + 4 <= length:
        # Search for sync byte
        if payload[idx] != 0xFF or (payload[idx + 1] & 0xE0) != 0xE0:
            idx += 1
            continue

        header_info = parse_mp3_header(payload[idx : idx + 4])
        if header_info is not None:
            frame_size, duration, _ = header_info
            # Guard against accidental sync in random bytes: check next frame if possible
            next_idx = idx + frame_size
            if next_idx + 4 <= length:
                next_header = parse_mp3_header(payload[next_idx : next_idx + 4])
                if next_header is None and valid_frames == 0 and not data.startswith(b"ID3"):
                    idx += 1
                    continue

            total_duration += duration
            valid_frames += 1
            idx += frame_size
        else:
            idx += 1

    return total_duration if valid_frames > 0 else 0.0


def parse_audio_duration(chunk: bytes, default_sample_rate: int = 24000) -> float:
    """Compute precise duration of an audio chunk.

    - If chunk contains valid MP3 frames, parses headers for exact MP3 duration.
    - Otherwise defaults to raw PCM 16-bit signed mono (default 24kHz = 48000 bytes/sec).
    """
    if not chunk:
        return 0.0
    mp3_dur = parse_mp3_duration(chunk)
    if mp3_dur > 0.0:
        return mp3_dur
    # Fallback: PCM 16-bit signed mono (2 bytes per sample)
    bytes_per_sec = default_sample_rate * 2
    return len(chunk) / float(bytes_per_sec)
