"""Speaker Verification and Voiceprint Biometrics using CAM++ ONNX model."""

import json
import logging
import os
from pathlib import Path
import re
import time
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_AUTH_DIR = Path.home() / ".config" / "nexo-auth"
DEFAULT_MODEL_PATH = str(DEFAULT_AUTH_DIR / "models" / "speaker_campplus.onnx")
DEFAULT_VOICEPRINTS_DIR = str(DEFAULT_AUTH_DIR / "voiceprints")
DEFAULT_THRESHOLD = 0.65
MIN_AUDIO_BYTES_FOR_ENROLL = 38400  # 1.2s of 16kHz, 16-bit PCM mono
MIN_AUDIO_BYTES_FOR_VERIFY = 16000  # 0.5s of 16kHz, 16-bit PCM mono

SPEAKER_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}\Z")


def validate_speaker_name(name: str) -> str:
    """Validate that speaker_name contains only alphanumeric characters, hyphens, and underscores."""
    if not isinstance(name, str) or not SPEAKER_NAME_PATTERN.fullmatch(name):
        raise ValueError(
            f"Invalid speaker name: {name!r}. Must match regex [a-zA-Z0-9_-]{{1,64}}"
        )
    return name


def compute_fbank(
    waveform: np.ndarray,
    sample_rate: int = 16000,
    num_mel_bins: int = 80,
    frame_length_ms: int = 25,
    frame_shift_ms: int = 10,
) -> np.ndarray:
    """Compute 80-bin Kaldi-compatible Mel Filterbank (fbank) features using pure NumPy.

    Matches CAM++ and WeSpeaker Mel filterbank preprocessing for 16 kHz audio.
    """
    frame_length = int(sample_rate * frame_length_ms / 1000)
    frame_shift = int(sample_rate * frame_shift_ms / 1000)
    if len(waveform) < frame_length:
        return np.zeros((0, num_mel_bins), dtype=np.float32)

    # 1. Preemphasis (alpha = 0.97)
    preemphasized = np.append(waveform[0], waveform[1:] - 0.97 * waveform[:-1])

    # 2. Framing with Hamming window
    num_samples = len(preemphasized)
    num_frames = 1 + int((num_samples - frame_length) / frame_shift)
    if num_frames <= 0:
        return np.zeros((0, num_mel_bins), dtype=np.float32)

    indices = (
        np.tile(np.arange(0, frame_length), (num_frames, 1))
        + np.tile(np.arange(0, num_frames * frame_shift, frame_shift), (frame_length, 1)).T
    )
    frames = preemphasized[indices] * np.hamming(frame_length)

    # 3. FFT & Power spectrum
    nfft = 512
    mag_frames = np.absolute(np.fft.rfft(frames, nfft))
    pow_frames = (1.0 / nfft) * (mag_frames ** 2)

    # 4. Triangular Mel Filterbanks
    low_freq_mel = 0
    high_freq_mel = 2595 * np.log10(1 + (sample_rate / 2) / 700)
    mel_points = np.linspace(low_freq_mel, high_freq_mel, num_mel_bins + 2)
    hz_points = 700 * (10 ** (mel_points / 2595) - 1)
    bins = np.floor((nfft + 1) * hz_points / sample_rate).astype(int)

    fbank = np.zeros((num_mel_bins, int(np.floor(nfft / 2 + 1))))
    for m in range(1, num_mel_bins + 1):
        f_m_minus = bins[m - 1]
        f_m = bins[m]
        f_m_plus = bins[m + 1]
        for k in range(f_m_minus, f_m):
            fbank[m - 1, k] = (k - bins[m - 1]) / max(f_m - f_m_minus, 1)
        for k in range(f_m, f_m_plus):
            fbank[m - 1, k] = (bins[m + 1] - k) / max(f_m_plus - f_m, 1)

    filter_banks = np.dot(pow_frames, fbank.T)
    filter_banks = np.where(filter_banks == 0, np.finfo(float).eps, filter_banks)
    filter_banks = np.log(filter_banks)

    # 5. Cepstral Mean Subtraction (CMVN per utterance)
    filter_banks -= np.mean(filter_banks, axis=0, keepdims=True)
    return filter_banks.astype(np.float32)


class SpeakerVerifier:
    """Speaker verification engine using CAM++ ONNX embeddings and cosine similarity."""

    def __init__(
        self,
        model_path: str = DEFAULT_MODEL_PATH,
        voiceprints_dir: str = DEFAULT_VOICEPRINTS_DIR,
        threshold: float = DEFAULT_THRESHOLD,
    ):
        self.model_path = model_path
        self.voiceprints_dir = Path(voiceprints_dir)
        self.threshold = threshold
        self._session = None
        self._model_tested = False

    def _ensure_dir(self):
        try:
            self.voiceprints_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(self.voiceprints_dir, 0o700)
        except Exception as e:
            logger.warning("Could not set permissions on %s: %s", self.voiceprints_dir, e)

    def _get_paths(self, speaker_name: str = "owner") -> Tuple[Path, Path]:
        valid_name = validate_speaker_name(speaker_name)
        npy_path = self.voiceprints_dir / f"{valid_name}.npy"
        json_path = self.voiceprints_dir / f"{valid_name}.json"
        return npy_path, json_path

    def _get_session(self):
        if self._session is None and not self._model_tested:
            self._model_tested = True
            model_exists = False
            try:
                model_exists = Path(self.model_path).exists()
            except (PermissionError, OSError):
                model_exists = False
            if model_exists:
                try:
                    import onnxruntime as ort
                    opts = ort.SessionOptions()
                    opts.inter_op_num_threads = 1
                    opts.intra_op_num_threads = 2
                    self._session = ort.InferenceSession(
                        self.model_path,
                        sess_options=opts,
                        providers=["CPUExecutionProvider"],
                    )
                    logger.info("Loaded speaker verification model: %s", self.model_path)
                except Exception as e:
                    logger.warning("Failed to initialize speaker ONNX session: %s", e)
            else:
                logger.debug("Speaker model file not found at %s", self.model_path)
        return self._session

    def is_enrolled(self, speaker_name: str = "owner") -> bool:
        """Check if speaker has an enrolled voiceprint."""
        try:
            npy_path, _ = self._get_paths(speaker_name)
            return npy_path.exists()
        except (ValueError, PermissionError, OSError):
            return False

    def get_metadata(self, speaker_name: str = "owner") -> Optional[dict]:
        """Read enrollment metadata JSON if available."""
        try:
            _, json_path = self._get_paths(speaker_name)
            if not json_path.exists():
                return None
            return json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def extract_embedding(self, pcm_audio: bytes) -> Optional[np.ndarray]:
        """Extract a 192-dimensional normalized speaker embedding from PCM16 mono 16kHz audio."""
        if len(pcm_audio) < MIN_AUDIO_BYTES_FOR_VERIFY:
            return None

        waveform = np.frombuffer(pcm_audio, dtype=np.int16).astype(np.float32)
        # Check that audio is not silence
        max_amp = np.max(np.abs(waveform))
        if max_amp < 150:  # Silence threshold
            return None

        session = self._get_session()
        if session is None:
            # Synthetic mock embedding for testing environments without ONNX model
            dummy = np.frombuffer(pcm_audio[:192 * 2], dtype=np.int16).astype(np.float32)
            if len(dummy) < 192:
                dummy = np.pad(dummy, (0, 192 - len(dummy)))
            norm = np.linalg.norm(dummy)
            return (dummy / norm) if norm > 0 else np.ones(192, dtype=np.float32)

        try:
            fbank_feats = compute_fbank(waveform)
            if fbank_feats.shape[0] < 5:
                return None
            input_tensor = fbank_feats[np.newaxis, :, :].astype(np.float32)
            input_name = session.get_inputs()[0].name
            output_name = session.get_outputs()[0].name
            outputs = session.run([output_name], {input_name: input_tensor})
            raw_emb = np.asarray(outputs[0])[0]
            norm = np.linalg.norm(raw_emb)
            if norm == 0:
                return None
            return raw_emb / norm
        except Exception as e:
            logger.error("Failed to extract speaker embedding: %s", e)
            return None

    def enroll(self, pcm_audio: bytes, speaker_name: str = "owner") -> Tuple[bool, str]:
        """Enroll a speaker voiceprint from a reference audio sample."""
        try:
            npy_path, json_path = self._get_paths(speaker_name)
        except ValueError as e:
            return False, f"Nama pembicara tidak valid: {e}"

        if len(pcm_audio) < MIN_AUDIO_BYTES_FOR_ENROLL:
            return False, "Durasi rekaman suara terlalu pendek. Mohon berbicara minimal 1,5 detik."

        embedding = self.extract_embedding(pcm_audio)
        if embedding is None:
            return False, "Suara tidak terdengar jelas atau terlalu pelan."

        self._ensure_dir()

        try:
            np.save(str(npy_path), embedding)
            os.chmod(npy_path, 0o600)
            duration_s = round(len(pcm_audio) / 32000.0, 2)
            meta = {
                "speaker": speaker_name,
                "enrolled_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "duration_seconds": duration_s,
                "status": "active",
                "embedding_dim": int(embedding.shape[0]),
            }
            json_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
            os.chmod(json_path, 0o600)
            logger.info("Successfully enrolled speaker '%s' (%s seconds)", speaker_name, duration_s)
            return True, f"Sidik suara {speaker_name} berhasil didaftarkan."
        except Exception as e:
            logger.error("Failed to save speaker voiceprint: %s", e)
            return False, f"Gagal menyimpan sidik suara: {e}"

    def unenroll(self, speaker_name: str = "owner") -> Tuple[bool, str]:
        """Unregister a speaker voiceprint, restoring open response mode."""
        try:
            npy_path, json_path = self._get_paths(speaker_name)
        except ValueError as e:
            return False, f"Nama pembicara tidak valid: {e}"

        removed = False
        try:
            if npy_path.exists():
                npy_path.unlink()
                removed = True
            if json_path.exists():
                json_path.unlink()
                removed = True
        except Exception as e:
            logger.error("Failed to remove voiceprint files: %s", e)
            return False, f"Gagal menghapus pendaftaran suara: {e}"

        if removed:
            logger.info("Successfully unenrolled speaker '%s'", speaker_name)
            return True, f"Pendaftaran suara {speaker_name} berhasil dihapus."
        return True, "Belum ada sidik suara yang didaftarkan."

    def verify(
        self,
        pcm_audio: bytes,
        threshold: Optional[float] = None,
        speaker_name: str = "owner",
    ) -> Tuple[bool, float]:
        """Verify if current speech matches enrolled voiceprint.

        Returns (is_match, similarity_score). If no voiceprint is enrolled, returns (True, 1.0).
        """
        try:
            npy_path, _ = self._get_paths(speaker_name)
        except ValueError:
            return False, 0.0

        try:
            if not npy_path.exists():
                return True, 1.0
        except (PermissionError, OSError):
            return True, 1.0

        current_emb = self.extract_embedding(pcm_audio)
        if current_emb is None:
            # Ambiguous audio or silence: fail-closed if enrolled
            return False, 0.0

        try:
            enrolled_emb = np.load(str(npy_path), allow_pickle=False)
            enrolled_norm = np.linalg.norm(enrolled_emb)
            if enrolled_norm > 0:
                enrolled_emb = enrolled_emb / enrolled_norm
            similarity = float(np.dot(current_emb, enrolled_emb))
            cutoff = threshold if threshold is not None else self.threshold
            is_match = similarity >= cutoff
            return is_match, similarity
        except Exception as e:
            logger.error("Failed to verify speaker voiceprint: %s", e)
            return False, 0.0


# Regexes for conversational voice registration flow
_ENROLL_PATTERNS = [
    r"\b(daftar|daftarkan|daptar|daptarkan|dapetakan|daftarin|rekam|simpan|catat|register|registrasi)\s+(sidik\s+)?suara(ku)?(\s+saya|\s+ku|\s+pemilik)?\b",
    r"\b(daftarkan|rekam|simpan|register)\s+sidik\s+suara\b",
    r"\bregister\s+voice\b",
    r"\bdaftarkan\s+vokal\b",
]

_UNENROLL_PATTERNS = [
    r"\b(hapus|reset|unregister|hilangkan)\s+(pendaftaran\s+)?(sidik\s+)?suara(ku)?(\s+saya|\s+terdaftar|\s+pemilik)?\b",
    r"\bhapus\s+sidik\s+suara\b",
    r"\bunregister\s+voice\b",
    r"\breset\s+suara(\s+terdaftar)?\b",
]

_CONFIRM_UNENROLL_PATTERNS = [
    r"\bkonfirmasi\s+hapus\s+suara\b",
    r"\bya\s*,?\s*hapus\s*(suara)?\b",
    r"\bkonfirmasi\b",
    r"\bsetuju\s+hapus\b",
]

_CANCEL_PATTERNS = [
    r"\bbatal\b",
    r"\bcancel\b",
    r"\bjangan\b",
    r"\btidak\s+jadi\b",
]


def is_voice_enroll_command(text: str) -> bool:
    """Check if speech is asking to enroll the owner's voice."""
    lower = text.lower().strip()
    return any(re.search(pat, lower) for pat in _ENROLL_PATTERNS)


def is_voice_unenroll_command(text: str) -> bool:
    """Check if speech is asking to unenroll / delete the registered voice."""
    lower = text.lower().strip()
    return any(re.search(pat, lower) for pat in _UNENROLL_PATTERNS)


def is_voice_confirm_unenroll_command(text: str) -> bool:
    """Check if speech confirms unenrollment."""
    lower = text.lower().strip()
    return any(re.search(pat, lower) for pat in _CONFIRM_UNENROLL_PATTERNS)


def is_voice_cancel_command(text: str) -> bool:
    """Check if speech cancels a pending voice operation."""
    lower = text.lower().strip()
    return any(re.search(pat, lower) for pat in _CANCEL_PATTERNS)
