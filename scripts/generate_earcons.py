#!/usr/bin/env python3
"""Generate high-quality earcon chime audio files (16-bit PCM WAV) for Nexo Android Appliance."""

import math
import struct
import wave
from pathlib import Path

SAMPLE_RATE = 44100

def generate_tone(frequencies, duration_s, sample_rate=SAMPLE_RATE, max_amplitude=0.35):
    """Generate audio samples with smooth envelope and optional harmonic warmth."""
    num_samples = int(duration_s * sample_rate)
    samples = []
    
    # Attack: 8ms, Release: 60ms
    attack_samples = int(0.008 * sample_rate)
    decay_start = int((duration_s - 0.08) * sample_rate)
    
    for i in range(num_samples):
        t = i / sample_rate
        # Calculate amplitude envelope
        if i < attack_samples:
            env = i / attack_samples
        elif i >= decay_start:
            env = max(0.0, 1.0 - (i - decay_start) / (num_samples - decay_start))
            env = env * env  # Exponential decay
        else:
            # Gentle sustain decay
            sustain_progress = (i - attack_samples) / max(1, (decay_start - attack_samples))
            env = 1.0 - 0.3 * sustain_progress
        
        # Superposition of frequencies
        val = 0.0
        for f, weight in frequencies:
            # Fundamental + soft second harmonic (warmth)
            sine = math.sin(2.0 * math.pi * f * t) + 0.18 * math.sin(4.0 * math.pi * f * t)
            val += sine * weight
        
        val = val * env * max_amplitude
        # Clamp to 16-bit PCM range
        val = max(-1.0, min(1.0, val))
        samples.append(int(val * 32767.0))
        
    return samples

def write_wav(path: Path, samples, sample_rate=SAMPLE_RATE):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as wav:
        wav.setnchannels(1)      # Mono
        wav.setsampwidth(2)      # 16-bit
        wav.setframerate(sample_rate)
        raw_bytes = struct.pack(f'<{len(samples)}h', *samples)
        wav.writeframes(raw_bytes)
    print(f"Written: {path} ({len(samples)} samples, {len(samples)/sample_rate:.2f}s)")

def main():
    res_raw = Path(__file__).resolve().parents[1] / "android/app/src/main/res/raw"
    res_raw.mkdir(parents=True, exist_ok=True)
    
    # 1. Listening Chime: Pleasant, friendly two-tone rising chime (E5 -> B5)
    # Part 1: E5 (659.25 Hz)
    p1 = generate_tone([(659.25, 0.9), (1318.5, 0.15)], duration_s=0.10, max_amplitude=0.32)
    # Part 2: B5 (987.77 Hz) with bell decay
    p2 = generate_tone([(987.77, 0.95), (1975.54, 0.12)], duration_s=0.22, max_amplitude=0.36)
    listening_samples = p1 + p2
    
    # 2. Thinking Chime: Soft, focused subtle ping (A5 -> D6 harmonic resolution)
    t1 = generate_tone([(880.0, 0.85), (1174.66, 0.35)], duration_s=0.18, max_amplitude=0.26)
    thinking_samples = t1
    
    # Write both chime_*.wav and earcon_*.wav aliases
    write_wav(res_raw / "chime_listening.wav", listening_samples)
    write_wav(res_raw / "chime_thinking.wav", thinking_samples)
    write_wav(res_raw / "earcon_listening.wav", listening_samples)
    write_wav(res_raw / "earcon_thinking.wav", thinking_samples)
    print("All earcon audio files generated successfully.")

if __name__ == "__main__":
    main()
