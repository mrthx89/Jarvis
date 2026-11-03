"""Model-free enrollment policy checks; synthetic tones are not speech evidence."""

import numpy as np

from nexo_server.pipeline import enrollment_quality as quality


def tone(seconds=3.0, amplitude=4000):
    t = np.arange(round(seconds * 16000)) / 16000
    return (amplitude * np.sin(2 * np.pi * 200 * t)).astype("<i2").tobytes()


def vector(index=0):
    embedding = np.zeros(192)
    embedding[index] = 1.0
    return embedding


def test_rejects_malformed_pcm():
    assert quality.assess_pcm(b"\x00") == "invalid_pcm"


def test_requires_two_to_six_seconds_inclusive():
    for pcm in (b"", tone(1.999), tone(6.001)):
        assert quality.assess_pcm(pcm) == "invalid_duration"
    for pcm in (tone(2), tone(6)):
        assert quality.assess_pcm(pcm) is None


def test_rejects_insufficient_active_energy_duration():
    for pcm in (tone(3, 0), tone(3, 100), tone(1) + tone(2, 0)):
        assert quality.assess_pcm(pcm) == "insufficient_voiced_duration"
    assert quality.assess_pcm(tone(1.5) + tone(1.5, 0)) is None


def test_requires_at_least_half_energy_active_frames():
    assert quality.assess_pcm(tone(1.5) + tone(4.5, 0)) == "insufficient_voiced_fraction"
    assert quality.assess_pcm(tone(3) + tone(3, 0)) is None


def test_rejects_one_percent_near_full_scale_clipping():
    samples = np.frombuffer(tone(), dtype="<i2").copy()
    samples[:480] = -32768
    assert quality.assess_pcm(samples.tobytes()) == "clipping"
    samples[:480] = 32767
    assert quality.assess_pcm(samples.tobytes()) == "clipping"


def test_rejects_dc_offset_of_five_percent_full_scale():
    samples = np.frombuffer(tone(), dtype="<i2").astype(np.int32)
    for offset in (-2000, 2000):
        assert quality.assess_pcm((samples + offset).astype("<i2").tobytes()) == "dc_offset"
    assert quality.assess_pcm((samples + 500).astype("<i2").tobytes()) is None


def test_normalizes_192_dimensional_embedding_without_mutation():
    original = np.arange(1, 193, dtype=np.float32)
    before = original.copy()
    normalized = quality.normalize_embedding(original)
    assert normalized is not None
    assert normalized.shape == (192,)
    assert np.isfinite(normalized).all()
    assert np.isclose(np.linalg.norm(normalized), 1)
    np.testing.assert_allclose(normalized, original / np.linalg.norm(original), rtol=1e-6)
    np.testing.assert_array_equal(original, before)


def test_rejects_invalid_embeddings():
    for invalid in (np.ones(191), np.ones((1, 192)), None, "bad", np.zeros(192),
                    np.full(192, np.nan), np.full(192, np.inf), [1j] * 192):
        assert quality.normalize_embedding(invalid) is None


def test_prepares_unweighted_normalized_centroid_excluding_holdout():
    a = vector()
    b = 0.8 * vector() + 0.6 * vector(1)
    holdout = 0.9 * vector() + np.sqrt(0.19) * vector(1)
    result, code = quality.prepare_profile([(a * 5, 64000), (b * 2, 192000)], (holdout, 96000))
    assert code == "ok"
    assert result is not None
    np.testing.assert_allclose(result, (a + b) / np.linalg.norm(a + b))


def test_requires_two_to_five_training_samples():
    for count in (0, 1, 6):
        assert quality.prepare_profile([(vector(), 96000)] * count, (vector(), 96000)) == (None, "invalid_sample_count")
    for count in (2, 5):
        result, code = quality.prepare_profile([(vector(), 96000)] * count, (vector(), 96000))
        assert result is not None and code == "ok"


def test_rejects_invalid_sample_or_holdout_byte_counts():
    valid = (vector(), 96000)
    for count in (True, 64000.0, -1, 63998, 192002, 96001):
        assert quality.prepare_profile([(vector(), count), valid], valid) == (None, "invalid_duration")
        assert quality.prepare_profile([valid, valid], (vector(), count)) == (None, "invalid_duration")


def test_rejects_invalid_training_or_holdout_embedding():
    valid = (vector(), 96000)
    for invalid in (None, np.zeros(192), np.ones(191), np.full(192, np.nan)):
        assert quality.prepare_profile([(invalid, 96000), valid], valid) == (None, "invalid_embedding")
        assert quality.prepare_profile([valid, valid], (invalid, 96000)) == (None, "invalid_embedding")


def test_rejects_any_inconsistent_training_pair_even_if_centroid_matches():
    center = vector()
    left = 0.8 * center + 0.6 * vector(1)
    right = 0.8 * center - 0.6 * vector(1)
    result, code = quality.prepare_profile(
        [(center, 96000), (left, 96000), (right, 96000)], (center, 96000))
    assert result is None and code == "sample_mismatch"


def test_holdout_must_match_each_sample_not_only_centroid():
    a = vector()
    b = 0.8 * a + 0.6 * vector(1)
    holdout = 0.95 * a - np.sqrt(0.0975) * vector(1)
    result, code = quality.prepare_profile([(a, 96000), (b, 96000)], (holdout, 96000))
    assert result is None and code == "holdout_mismatch"


def test_threshold_cannot_be_weakened_or_nonfinite():
    valid = (vector(), 96000)
    for threshold in (0.64, float("nan"), float("inf"), 1.01, True, "0.65"):
        result, code = quality.prepare_profile([valid, valid], valid, threshold=threshold)
        assert result is None and code == "invalid_threshold"


def test_rejects_malformed_sample_or_missing_holdout():
    valid = (vector(), 96000)
    for malformed in (None, (), (vector(),), (vector(), 96000, 1), "bad"):
        assert quality.prepare_profile([valid, malformed], valid) == (None, "invalid_sample")
        assert quality.prepare_profile([valid, valid], malformed) == (None, "invalid_sample")


def test_rejects_complex_array_instead_of_discarding_imaginary_part():
    assert quality.normalize_embedding(np.ones(192, dtype=np.complex128) * (1 + 1j)) is None
