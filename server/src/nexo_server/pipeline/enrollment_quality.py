"""Model-free enrollment checks for unmodified PCM16 mono at 16 kHz.

These energy heuristics are not speech VAD, intelligibility or SNR estimates.
Embeddings must come from speaker.py's pinned model and frontend; this module
neither extracts embeddings nor changes/persists profiles.
"""


import numpy as np

from .speaker import DEFAULT_THRESHOLD


def assess_pcm(pcm: bytes) -> str | None:
    """Check 2..6 s PCM; never alter the bytes sent to the speaker frontend.

    Require >=1.5 s and >=50% energy-active ("voiced") 20 ms frames. The
    frame-centered RMS floor is max(200 PCM units, 20% of p90 frame RMS).
    Reject >=1% samples at |amplitude| >=32760 or DC >=5% full scale.
    These conservative, uncalibrated heuristics can accept noise or tones;
    "voiced" does not establish speech, speaker identity or clean audio.
    """
    if not isinstance(pcm, bytes) or len(pcm) % 2:
        return "invalid_pcm"
    if not 64000 <= len(pcm) <= 192000:
        return "invalid_duration"
    waveform = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
    if np.count_nonzero(np.abs(waveform) >= 32760) >= len(waveform) * 0.01:
        return "clipping"
    if abs(float(waveform.mean())) >= 32768 * 0.05:
        return "dc_offset"
    frames = waveform[:len(waveform) // 320 * 320].reshape(-1, 320)
    frame_rms = np.sqrt(np.mean((frames - frames.mean(axis=1, keepdims=True)) ** 2, axis=1))
    energy_floor = max(200.0, float(np.percentile(frame_rms, 90)) * 0.2)
    voiced_frames = int(np.count_nonzero(frame_rms >= energy_floor))
    if voiced_frames < 75:  # 1.5 s of 20 ms energy-active frames.
        return "insufficient_voiced_duration"
    if voiced_frames * 320 < len(waveform) * 0.5:
        return "insufficient_voiced_fraction"
    return None


def normalize_embedding(embedding) -> np.ndarray | None:
    """Return a fresh unit vector for a pinned CAM++ embedding."""
    try:
        if np.iscomplexobj(embedding):
            return None
        vector = np.asarray(embedding, dtype=np.float64)
    except (TypeError, ValueError, OverflowError):
        return None
    if vector.shape != (192,) or not np.isfinite(vector).all():
        return None
    norm = np.linalg.norm(vector)
    if not np.isfinite(norm) or norm == 0:
        return None
    return vector / norm


def prepare_profile(samples: list[tuple[np.ndarray, int]], holdout,
                    threshold: float = DEFAULT_THRESHOLD) -> tuple[np.ndarray | None, str]:
    """Return (unit centroid, "ok") or (None, rejection code).

    Accept 2..5 training samples plus one holdout, each (embedding, byte count).
    Call assess_pcm before embedding extraction: byte counts cannot prove quality.
    All training pairs and every holdout/training pair must meet threshold;
    holdout never enters the centroid. Caller must supply distinct utterance IDs
    and enforce the pinned speaker.py FRONTEND_ID/MODEL_SHA256 provenance, active
    ownership and explicit Save; raw vectors cannot establish that provenance.
    """
    if (isinstance(threshold, bool) or not isinstance(threshold, (int, float))
            or not np.isfinite(threshold) or not DEFAULT_THRESHOLD <= threshold <= 1):
        return None, "invalid_threshold"
    if not isinstance(samples, list) or not 2 <= len(samples) <= 5:
        return None, "invalid_sample_count"
    for sample in [*samples, holdout]:
        if not isinstance(sample, (tuple, list)) or len(sample) != 2:
            return None, "invalid_sample"
        _, audio_bytes = sample
        if type(audio_bytes) is not int or not 64000 <= audio_bytes <= 192000 or audio_bytes % 2:
            return None, "invalid_duration"
    embeddings = []
    for embedding, _ in samples:
        normalized = normalize_embedding(embedding)
        if normalized is None:
            return None, "invalid_embedding"
        embeddings.append(normalized)
    held_out = normalize_embedding(holdout[0])
    if held_out is None:
        return None, "invalid_embedding"
    for index, embedding in enumerate(embeddings):
        if any(float(embedding @ other) < threshold for other in embeddings[index + 1:]):
            return None, "sample_mismatch"
    if any(float(held_out @ embedding) < threshold for embedding in embeddings):
        return None, "holdout_mismatch"
    return normalize_embedding(np.mean(embeddings, axis=0)), "ok"
