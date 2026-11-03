"""Speaker Verification and Voiceprint Biometrics using CAM++ ONNX model."""

import hashlib
import json
import logging
import os
from pathlib import Path
import re
import time
import tempfile
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_AUTH_DIR = Path.home() / ".config" / "nexo-auth"
DEFAULT_MODEL_PATH = str(DEFAULT_AUTH_DIR / "models" / "speaker_campplus.onnx")
DEFAULT_VOICEPRINTS_DIR = str(DEFAULT_AUTH_DIR / "voiceprints")
MODEL_SHA256 = "f682b514c05d947ee3fa91cd6ec6c5c7543479a128373fa29b1faedccd21fd11"
FRONTEND_ID = "sherpa-3dspeaker-knf-1.22.5-v1"
MODEL_METADATA = {
    "normalize_samples": "1", "output_dim": "192",
    "feature_normalize_type": "global-mean", "sample_rate": "16000",
    "framework": "3d-speaker", "language": "Chinese",
}
REENROLL_MESSAGE = "Sidik suara lama tidak kompatibel. Konfirmasi hapus lalu daftarkan ulang suara Anda."
DEFAULT_THRESHOLD = 0.65
MIN_AUDIO_BYTES_FOR_ENROLL = 64000  # 2s of 16kHz, 16-bit PCM mono
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
    """Sherpa 3D-Speaker frontend: normalized float PCM to mean-centered log Mel.

    Pinned to kaldi-native-fbank 1.22.5; see docs/SPEAKER_MODEL.md for the
    reference contract. Short clips remain empty for existing callers.
    """
    import kaldi_native_fbank as knf

    if (sample_rate, num_mel_bins, frame_length_ms, frame_shift_ms) != (16000, 80, 25, 10):
        raise ValueError("CAM++ requires 16 kHz, 80 bins and 25/10 ms frames")
    waveform = np.asarray(waveform, dtype=np.float32)
    if waveform.ndim != 1 or not np.isfinite(waveform).all():
        raise ValueError("Expected finite mono waveform")
    if len(waveform) < 400:
        return np.zeros((0, 80), dtype=np.float32)
    config = knf.FbankOptions()
    config.frame_opts.samp_freq = 16000
    config.frame_opts.frame_length_ms = 25
    config.frame_opts.frame_shift_ms = 10
    config.frame_opts.dither = 0
    config.frame_opts.snip_edges = False
    config.frame_opts.remove_dc_offset = True
    config.frame_opts.preemph_coeff = 0.97
    config.frame_opts.window_type = "povey"
    config.frame_opts.round_to_power_of_two = True
    config.mel_opts.num_bins = 80
    config.mel_opts.low_freq = 20
    config.mel_opts.high_freq = -400
    config.mel_opts.is_librosa = False
    config.use_energy = False
    config.use_log_fbank = True
    config.use_power = True
    fbank = knf.OnlineFbank(config)
    fbank.accept_waveform(16000, waveform.tolist())
    fbank.input_finished()
    features = np.stack([fbank.get_frame(i) for i in range(fbank.num_frames_ready)])
    return (features - features.mean(axis=0, keepdims=True)).astype(np.float32)


def validate_model_session(session) -> None:
    """Reject tensor/metadata contracts other than the pinned Sherpa CAM++ export."""
    metadata = session.get_modelmeta().custom_metadata_map
    if any(metadata.get(key) != value for key, value in MODEL_METADATA.items()):
        raise ValueError("Incompatible CAM++ model metadata")
    inputs, outputs = session.get_inputs(), session.get_outputs()
    if (len(inputs) != 1 or len(outputs) != 1
            or inputs[0].name != "x" or inputs[0].type != "tensor(float)"
            or len(inputs[0].shape) != 3 or inputs[0].shape[-1] != 80
            or outputs[0].name != "embedding" or outputs[0].type != "tensor(float)"
            or len(outputs[0].shape) != 2 or outputs[0].shape[-1] != 192):
        raise ValueError("Incompatible CAM++ tensor contract")


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
        self._validated_model = False

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
                    if hashlib.sha256(Path(self.model_path).read_bytes()).hexdigest() != MODEL_SHA256:
                        raise ValueError("Untrusted CAM++ model checksum; see docs/SPEAKER_MODEL.md")
                    import kaldi_native_fbank  # Require the real frontend before enabling enrollment.
                    import onnxruntime as ort
                    opts = ort.SessionOptions()
                    opts.inter_op_num_threads = 1
                    opts.intra_op_num_threads = 2
                    session = ort.InferenceSession(
                        self.model_path,
                        sess_options=opts,
                        providers=["CPUExecutionProvider"],
                    )
                    validate_model_session(session)
                    self._validated_model = True
                    self._session = session
                    logger.info("Loaded speaker verification model: %s", self.model_path)
                except Exception as e:
                    logger.warning("Failed to initialize speaker ONNX session: %s", e)
            else:
                logger.debug("Speaker model file not found at %s", self.model_path)
        return self._session

    def is_available(self) -> bool:
        return self._get_session() is not None

    def is_enrolled(self, speaker_name: str = "owner") -> bool:
        """Check if speaker has an enrolled voiceprint."""
        try:
            npy_path, _ = self._get_paths(speaker_name)
            npy_path.stat()
            return True
        except FileNotFoundError:
            return False
        except ValueError:
            return False

    def _profile_contract(self) -> dict:
        return {
            "profile_version": 2, "frontend": FRONTEND_ID,
            "model_sha256": MODEL_SHA256 if self._validated_model else "unvalidated-injected-session",
            "embedding_dim": 192,
        }

    def get_metadata(self, speaker_name: str = "owner") -> Optional[dict]:
        """Read metadata; retain incompatible profiles and require explicit re-enrollment."""
        try:
            if not self.is_enrolled(speaker_name):
                return None
        except OSError:
            return {"status": "reenrollment_required", "message": REENROLL_MESSAGE}
        try:
            _, json_path = self._get_paths(speaker_name)
            meta = json.loads(json_path.read_text(encoding="utf-8"))
            if not isinstance(meta, dict):
                meta = {}
        except Exception:
            meta = {}
        self._get_session()
        if any(meta.get(key) != value for key, value in self._profile_contract().items()):
            return {**meta, "status": "reenrollment_required", "message": REENROLL_MESSAGE}
        return meta

    def extract_embedding(self, pcm_audio: bytes) -> Optional[np.ndarray]:
        """Extract a 192-dimensional normalized speaker embedding from PCM16 mono 16kHz audio."""
        if len(pcm_audio) < MIN_AUDIO_BYTES_FOR_VERIFY or len(pcm_audio) % 2:
            return None

        waveform = np.frombuffer(pcm_audio, dtype="<i2").astype(np.float32)
        # Check that audio is not silence
        max_amp = np.max(np.abs(waveform))
        if max_amp < 150:  # Silence threshold
            return None

        session = self._get_session()
        if session is None:
            return None

        try:
            fbank_feats = compute_fbank(waveform / 32768.0)
            if fbank_feats.shape[0] < 5:
                return None
            input_tensor = fbank_feats[np.newaxis, :, :].astype(np.float32)
            input_name = session.get_inputs()[0].name
            output_name = session.get_outputs()[0].name
            outputs = session.run([output_name], {input_name: input_tensor})
            output = np.asarray(outputs[0])
            if output.shape != (1, 192) or not np.isfinite(output).all():
                return None
            raw_emb = output[0]
            norm = np.linalg.norm(raw_emb)
            if norm == 0 or not np.isfinite(norm):
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

        if not self.is_available():
            return False, "Model sidik suara tidak tersedia."
        if len(pcm_audio) < MIN_AUDIO_BYTES_FOR_ENROLL or len(pcm_audio) > 192000:
            return False, "Durasi rekaman suara terlalu pendek. Mohon berbicara minimal 2 detik."

        embedding = self.extract_embedding(pcm_audio)
        if embedding is None:
            return False, "Suara tidak terdengar jelas atau terlalu pelan."

        return self.save_embedding(embedding, len(pcm_audio), speaker_name)

    def save_embedding(self, embedding: np.ndarray, audio_bytes: int, speaker_name: str = "owner") -> Tuple[bool, str]:
        npy_path, json_path = self._get_paths(speaker_name)
        staged = []
        metadata_promoted = False
        backup = None
        try:
            if (audio_bytes < MIN_AUDIO_BYTES_FOR_ENROLL or audio_bytes > 192000
                    or not self.is_available() or embedding.shape != (192,)
                    or not np.isfinite(embedding).all() or np.linalg.norm(embedding) == 0):
                return False, "Sampel sidik suara tidak valid."
            self.voiceprints_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(self.voiceprints_dir, 0o700)
            for _ in range(2):
                fd, name = tempfile.mkstemp(prefix=".voiceprint-", dir=self.voiceprints_dir)
                os.close(fd)
                staged.append(Path(name))
            with staged[0].open("wb") as stream:
                np.save(stream, embedding, allow_pickle=False)
            os.chmod(staged[0], 0o600)
            duration_s = round(audio_bytes / 32000.0, 2)
            meta = {
                "speaker": speaker_name,
                "enrolled_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "duration_seconds": duration_s,
                "status": "active",
                **self._profile_contract(),
            }
            staged[1].write_text(json.dumps(meta, indent=2), encoding="utf-8")
            os.chmod(staged[1], 0o600)
            if json_path.exists():
                backup = staged[1].with_suffix(".backup")
                os.link(json_path, backup)
            os.replace(staged[1], json_path)
            metadata_promoted = True
            # The biometric file is the commit point; never truncate the live profile.
            os.replace(staged[0], npy_path)
            logger.info("Successfully enrolled speaker '%s' (%s seconds)", speaker_name, duration_s)
            return True, f"Sidik suara {speaker_name} berhasil didaftarkan."
        except Exception as e:
            if metadata_promoted:
                try:
                    if backup:
                        os.replace(backup, json_path)
                    else:
                        json_path.unlink(missing_ok=True)
                except OSError:
                    logger.exception("Unable to restore voiceprint metadata")
            logger.error("Failed to save speaker voiceprint: %s", e)
            return False, "Gagal menyimpan sidik suara."
        finally:
            for path in staged + ([backup] if backup else []):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("Unable to remove staged voiceprint")

    def unenroll(self, speaker_name: str = "owner") -> Tuple[bool, str]:
        """Unregister a speaker voiceprint, restoring open response mode."""
        try:
            npy_path, json_path = self._get_paths(speaker_name)
        except ValueError as e:
            return False, f"Nama pembicara tidak valid: {e}"

        removed = False
        try:
            if json_path.exists():
                json_path.unlink()
                removed = True
            if npy_path.exists():
                npy_path.unlink()
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
            npy_path.stat()
        except FileNotFoundError:
            return True, 1.0
        except OSError:
            return False, 0.0

        metadata = self.get_metadata(speaker_name)
        if not metadata or metadata.get("status") == "reenrollment_required":
            logger.warning(REENROLL_MESSAGE)
            return False, 0.0

        current_emb = self.extract_embedding(pcm_audio)
        if current_emb is None:
            # Ambiguous audio or silence: fail-closed if enrolled
            return False, 0.0

        try:
            enrolled_emb = np.load(str(npy_path), allow_pickle=False)
            if enrolled_emb.shape != (192,) or not np.isfinite(enrolled_emb).all():
                return False, 0.0
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
