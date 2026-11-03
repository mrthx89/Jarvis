"""CAM++ frontend regression against the pinned Sherpa/KNF probe, not human speech."""

from pathlib import Path

import numpy as np
import pytest

from nexo_server.pipeline.speaker import SpeakerVerifier
from tests.test_voiceprint_enrollment_protocol import FakeONNXSession

from nexo_server.pipeline.speaker import compute_fbank


REFERENCE = Path(__file__).parent / "data" / "campplus_frontend_reference.npz"


def synthetic_pcm():
    rng = np.random.default_rng(20261103)
    t = np.arange(48000, dtype=np.float64) / 16000
    signal = (
        0.25 * (0.55 + 0.4 * np.sin(2 * np.pi * 2.2 * t))
        * np.sin(2 * np.pi * (110 * t + 95 * t * t))
        + 0.12 * np.sin(2 * np.pi * 1500 * t) * (t > 1.2)
        + (0.015 + 0.035 * t / 3) * rng.normal(size=t.size)
        + 0.02 * np.sin(2 * np.pi * 0.7 * t)
    )
    return np.rint(np.clip(signal, -1, 1) * 32767).astype("<i2")


def test_real_model_embedding_matches_reference(tmp_path):
    import os
    from nexo_server.pipeline.speaker import DEFAULT_MODEL_PATH

    model = Path(os.environ.get("NEXO_TEST_SPEAKER_MODEL", DEFAULT_MODEL_PATH))
    if not model.is_file():
        pytest.skip("Pinned CAM++ model not provisioned; synthetic fake-session tests still run")
    verifier = SpeakerVerifier(model_path=str(model), voiceprints_dir=str(tmp_path / "profiles"))
    assert verifier.is_available()
    with np.load(REFERENCE, allow_pickle=False) as reference:
        features = reference["features"]
    session = verifier._get_session()
    raw = session.run(["embedding"], {"x": features[None]})[0][0]
    assert raw.shape == (192,)
    assert np.isfinite(raw).all()
    assert float(np.linalg.norm(raw)) == pytest.approx(23.04254722595215, abs=1e-4)
    embedding = verifier.extract_embedding(synthetic_pcm().tobytes())
    assert embedding is not None
    np.testing.assert_allclose(embedding, raw / np.linalg.norm(raw), rtol=0, atol=1e-6)
    assert not (tmp_path / "profiles").exists()


def test_frontend_matches_sherpa_reference():
    with np.load(REFERENCE, allow_pickle=False) as reference:
        expected = reference["features"]
    actual = compute_fbank(synthetic_pcm().astype(np.float32) / 32768.0)
    assert actual.shape == (300, 80)
    assert actual.dtype == np.float32
    np.testing.assert_allclose(actual, expected, rtol=0, atol=2e-5)
    np.testing.assert_allclose(actual.mean(axis=0), 0, atol=1e-5)


@pytest.mark.parametrize("field,value", [("sample_rate", "8000"), ("normalize_samples", "0"), ("feature_normalize_type", "none")])
def test_model_metadata_mismatch_rejected(field, value):
    from types import SimpleNamespace
    from nexo_server.pipeline import speaker

    metadata = {**speaker.MODEL_METADATA, field: value}
    session = SimpleNamespace(get_modelmeta=lambda: SimpleNamespace(custom_metadata_map=metadata))
    with pytest.raises(ValueError, match="metadata"):
        speaker.validate_model_session(session)


def test_model_tensor_mismatch_rejected():
    from types import SimpleNamespace
    from nexo_server.pipeline import speaker

    session = SimpleNamespace(
        get_modelmeta=lambda: SimpleNamespace(custom_metadata_map=speaker.MODEL_METADATA),
        get_inputs=lambda: [SimpleNamespace(name="x", type="tensor(float)", shape=["N", "T", 40])],
        get_outputs=lambda: [SimpleNamespace(name="embedding", type="tensor(float)", shape=["N", 192])],
    )
    with pytest.raises(ValueError, match="tensor"):
        speaker.validate_model_session(session)


def test_model_hash_rejected_before_session_creation(tmp_path, monkeypatch):
    import onnxruntime as ort

    model = tmp_path / "untrusted.onnx"
    model.write_bytes(b"not the pinned model")
    calls = []
    monkeypatch.setattr(ort, "InferenceSession", lambda *a, **kw: calls.append(a) or FakeONNXSession())
    verifier = SpeakerVerifier(model_path=str(model), voiceprints_dir=str(tmp_path / "profiles"))
    assert not verifier.is_available()
    assert calls == []


def test_absent_profile_has_no_metadata(tmp_path):
    verifier = SpeakerVerifier(model_path=str(tmp_path / "missing"), voiceprints_dir=str(tmp_path))
    assert verifier.get_metadata() is None


@pytest.mark.parametrize("change", ["legacy", "missing", "corrupt", "frontend", "model", "dimension"])
def test_legacy_profile_fails_closed_with_reenroll_message(tmp_path, change):
    import json

    verifier = SpeakerVerifier(voiceprints_dir=str(tmp_path))
    verifier._session = FakeONNXSession()
    pcm = synthetic_pcm().tobytes()
    assert verifier.enroll(pcm)[0]
    profile = tmp_path / "owner.npy"
    before = profile.read_bytes()
    metadata_path = tmp_path / "owner.json"
    metadata = json.loads(metadata_path.read_text())
    if change == "missing":
        metadata_path.unlink()
    elif change == "corrupt":
        metadata_path.write_text("not json")
    else:
        if change == "legacy":
            metadata = {"speaker": "owner", "embedding_dim": 192}
        elif change == "frontend":
            metadata["frontend"] = "old-numpy"
        elif change == "model":
            metadata["model_sha256"] = "other-model"
        elif change == "dimension":
            metadata["embedding_dim"] = 256
        metadata_path.write_text(json.dumps(metadata))
    before_metadata = metadata_path.read_bytes() if metadata_path.exists() else None
    assert verifier.is_enrolled()
    assert verifier.verify(pcm) == (False, 0.0)
    metadata = verifier.get_metadata()
    assert metadata["status"] == "reenrollment_required"
    assert "daftarkan ulang" in metadata["message"].lower()
    assert profile.read_bytes() == before
    assert (metadata_path.read_bytes() if metadata_path.exists() else None) == before_metadata


def test_extractor_feeds_normalized_pcm_features(tmp_path, monkeypatch):
    from nexo_server.pipeline import speaker

    seen = []
    original = speaker.compute_fbank

    def capture(waveform):
        seen.append(waveform.copy())
        return original(waveform)

    monkeypatch.setattr(speaker, "compute_fbank", capture)
    class RecordingSession(FakeONNXSession):
        def run(self, outputs, inputs):
            self.features = next(iter(inputs.values()))
            return super().run(outputs, inputs)

    verifier = SpeakerVerifier(voiceprints_dir=str(tmp_path))
    session = RecordingSession()
    verifier._session = session
    assert verifier.extract_embedding(synthetic_pcm().tobytes()) is not None
    np.testing.assert_array_equal(seen[0], synthetic_pcm().astype(np.float32) / 32768.0)
    with np.load(REFERENCE, allow_pickle=False) as reference:
        np.testing.assert_allclose(session.features[0], reference["features"], rtol=0, atol=2e-5)

