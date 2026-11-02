"""Unit and integration tests for speaker verification, voiceprints, and biometric gating."""

import json
from pathlib import Path
import tempfile
import pytest
import numpy as np

from nexo_server.pipeline.speaker import (
    compute_fbank,
    validate_speaker_name,
    SpeakerVerifier,
    is_voice_enroll_command,
    is_voice_unenroll_command,
    is_voice_confirm_unenroll_command,
    is_voice_cancel_command,
)
from jarvis_server.pipeline.speaker import SpeakerVerifier as JarvisSpeakerVerifier


def _generate_pcm(duration_s: float = 2.0, freq_hz: float = 440.0, sample_rate: int = 16000) -> bytes:
    """Generate synthetic PCM16 mono audio tone."""
    num_samples = int(duration_s * sample_rate)
    t = np.linspace(0, duration_s, num_samples, dtype=np.float32)
    wave = 0.6 * np.sin(2 * np.pi * freq_hz * t)
    pcm16 = (wave * 32767).astype(np.int16)
    return pcm16.tobytes()


def test_validate_speaker_name():
    assert validate_speaker_name("owner") == "owner"
    assert validate_speaker_name("user_1") == "user_1"
    assert validate_speaker_name("admin-test") == "admin-test"

    for invalid in ["../passwd", "/root", "a/b", "..", "", "foo bar", "user$1", "a" * 65]:
        with pytest.raises(ValueError):
            validate_speaker_name(invalid)


def test_compute_fbank_dimensions():
    # 1 second of 16kHz audio
    waveform = np.sin(np.linspace(0, 100, 16000)).astype(np.float32)
    fbank = compute_fbank(waveform)
    assert fbank.ndim == 2
    assert fbank.shape[1] == 80
    assert fbank.shape[0] > 90  # ~98 frames for 1 sec with 25ms window and 10ms shift

    # Short waveform returns empty
    short = np.zeros(100, dtype=np.float32)
    assert compute_fbank(short).shape == (0, 80)


def test_voice_command_patterns():
    assert is_voice_enroll_command("daftarkan suara saya")
    assert is_voice_enroll_command("tolong daftarkan suara")
    assert is_voice_enroll_command("rekam suara pemilik")
    assert is_voice_enroll_command("register voice")
    assert not is_voice_enroll_command("halo nexo apa kabar")

    assert is_voice_unenroll_command("hapus suara saya")
    assert is_voice_unenroll_command("hapus sidik suara")
    assert is_voice_unenroll_command("reset suara terdaftar")
    assert is_voice_unenroll_command("unregister voice")
    assert not is_voice_unenroll_command("hapus file dokumen")

    assert is_voice_confirm_unenroll_command("konfirmasi hapus suara")
    assert is_voice_confirm_unenroll_command("ya, hapus")
    assert not is_voice_confirm_unenroll_command("batal")

    assert is_voice_cancel_command("batal")
    assert is_voice_cancel_command("tidak jadi")
    assert not is_voice_cancel_command("setuju")


def test_speaker_enrollment_and_verification_lifecycle(tmp_path):
    voiceprints_dir = tmp_path / "voiceprints"
    verifier = SpeakerVerifier(
        model_path="/root/.config/nexo-auth/models/speaker_campplus.onnx",
        voiceprints_dir=str(voiceprints_dir),
        threshold=0.65,
    )

    # 1. Unenrolled state: verification always returns True (open response mode)
    assert not verifier.is_enrolled("owner")
    pcm_sample = _generate_pcm(2.0, 440.0)
    match, score = verifier.verify(pcm_sample, speaker_name="owner")
    assert match is True
    assert score == 1.0

    # 2. Too short audio rejected
    short_pcm = _generate_pcm(0.5, 440.0)
    ok, msg = verifier.enroll(short_pcm, speaker_name="owner")
    assert ok is False
    assert "terlalu pendek" in msg

    # 3. Successful enrollment
    ok, msg = verifier.enroll(pcm_sample, speaker_name="owner")
    assert ok is True
    assert "berhasil" in msg
    assert verifier.is_enrolled("owner")

    # Check files created and permissions
    npy_file = voiceprints_dir / "owner.npy"
    json_file = voiceprints_dir / "owner.json"
    assert npy_file.exists()
    assert json_file.exists()

    meta = verifier.get_metadata("owner")
    assert meta is not None
    assert meta["speaker"] == "owner"
    assert meta["status"] == "active"
    assert meta["embedding_dim"] == 192

    # 4. Verify identical audio matches with high similarity
    match, score = verifier.verify(pcm_sample, speaker_name="owner")
    assert match is True
    assert score > 0.90

    # 5. Verify different voice/frequency has lower similarity
    diff_pcm = _generate_pcm(2.0, 1800.0)
    match, diff_score = verifier.verify(diff_pcm, speaker_name="owner", threshold=0.75)
    # The different audio should score lower than identical audio
    assert diff_score < score

    # 6. Unenrollment
    ok, msg = verifier.unenroll("owner")
    assert ok is True
    assert not verifier.is_enrolled("owner")
    assert not npy_file.exists()
    assert not json_file.exists()


def test_jarvis_speaker_compatibility(tmp_path):
    """Verify legacy jarvis_server speaker class matches nexo_server."""
    v_jarvis = JarvisSpeakerVerifier(voiceprints_dir=str(tmp_path))
    assert hasattr(v_jarvis, "enroll")
    assert hasattr(v_jarvis, "verify")
    assert hasattr(v_jarvis, "unenroll")


@pytest.mark.asyncio
async def test_websocket_speaker_gating(tmp_path):
    """Test that ApplianceSession gates requests based on speaker verification."""
    from unittest.mock import AsyncMock, MagicMock
    from nexo_server.gateway.websocket_handler import ApplianceSession
    from nexo_server.mascot import MascotExpression

    mock_ws = AsyncMock()
    mock_stt = AsyncMock()
    mock_tts = MagicMock()
    spoken = []
    async def fake_synthesize_stream(text):
        spoken.append(text)
        yield b"\xff\xfb\x90\x00" * 10
    mock_tts.synthesize_stream = fake_synthesize_stream
    mock_hermes = AsyncMock()
    mock_hub = MagicMock()

    verifier = SpeakerVerifier(
        model_path="/root/.config/nexo-auth/models/speaker_campplus.onnx",
        voiceprints_dir=str(tmp_path),
        threshold=0.65,
    )

    session = ApplianceSession(
        websocket=mock_ws,
        session_id="test_sess",
        stt=mock_stt,
        tts=mock_tts,
        hermes=mock_hermes,
        session_hub=mock_hub,
        speaker_verifier=verifier,
    )

    owner_pcm = _generate_pcm(2.0, 440.0)
    stranger_pcm = _generate_pcm(2.0, 2000.0)

    # 1. Voice command: enroll
    mock_stt.transcribe.return_value = "daftarkan suara saya"
    await session._run_pipeline(owner_pcm)
    assert verifier.is_enrolled("owner")
    assert any("berhasil didaftarkan" in s for s in spoken)

    # 2. General query from stranger (rejected)
    spoken.clear()
    mock_stt.transcribe.return_value = "buka pintu gerbang"
    # Mock verify to reject stranger
    verifier.verify = MagicMock(return_value=(False, 0.2))
    await session._run_pipeline(stranger_pcm)
    # Hermes must NOT be queried
    mock_hermes.query_stream.assert_not_called()
    # TTS speaks canned refusal
    assert any("tidak dikenali" in s for s in spoken)

    # 3. General query from owner (accepted)
    spoken.clear()
    mock_hermes.reset_mock()
    verifier.verify = MagicMock(return_value=(True, 0.95))
    mock_stt.transcribe.return_value = "apa kabar hari ini"
    async def mock_stream(prompt):
        yield "Kabar baik!"
    mock_hermes.query_stream = mock_stream
    await session._run_pipeline(owner_pcm)
    assert any("Kabar baik!" in s for s in spoken)

    # 4. Voice command: unenroll
    spoken.clear()
    mock_stt.transcribe.return_value = "hapus sidik suara"
    await session._run_pipeline(owner_pcm)
    assert not verifier.is_enrolled("owner")
    assert any("berhasil dihapus" in s for s in spoken)

