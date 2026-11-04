"""Speech-to-Text abstraction and engines."""

from abc import ABC, abstractmethod
import asyncio
from typing import Optional


class BaseSTT(ABC):
    """Abstract base class for Speech-To-Text engines."""

    @abstractmethod
    async def transcribe(self, pcm_audio: bytes, language: str = "id") -> str:
        """Transcribe raw 16kHz 16-bit PCM mono audio to text."""
        pass


class MockSTT(BaseSTT):
    """Mock STT for testing and verification without external dependencies."""

    def __init__(self, response_text: str = "Halo Nexo, apa status sistem saat ini?"):
        self.response_text = response_text

    async def transcribe(self, pcm_audio: bytes, language: str = "id") -> str:
        await asyncio.sleep(0.05)  # Simulate small processing delay
        return self.response_text


class FasterWhisperSTT(BaseSTT):
    """Local high-performance STT using faster-whisper (CTranslate2)."""

    def __init__(self, model_size: str = "base"):
        self.model_size = model_size
        self._model = None

    def _get_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
        return self._model

    async def transcribe(self, pcm_audio: bytes, language: str = "id") -> str:
        import numpy as np

        if len(pcm_audio) < 1600:
            return ""

        audio_array = np.frombuffer(pcm_audio, dtype=np.int16).astype(np.float32) / 32768.0

        # Remove DC bias if signal has AC variance (avoids zeroing synthetic constant test arrays)
        if float(np.std(audio_array)) > 1e-4:
            audio_array = audio_array - float(np.mean(audio_array))

        # Digital gain normalization: use 99.5th percentile so isolated mic clicks
        # do not suppress gain for soft/distant speech, with fallback to peak.
        abs_audio = np.abs(audio_array)
        peak = float(np.max(abs_audio))
        p99 = float(np.percentile(abs_audio, 99.5))
        ref_level = p99 if p99 >= 0.003 else peak
        if ref_level > 0.003:
            gain = min(0.95 / ref_level, 10.0)  # Up to +20 dB digital gain boost
            audio_array = np.clip(audio_array * gain, -1.0, 1.0).astype(np.float32)

        def _run_transcribe():
            model = self._get_model()
            segments, _ = model.transcribe(
                audio_array,
                language=language,
                beam_size=2,
                no_speech_threshold=0.65,
                condition_on_previous_text=False,
                repetition_penalty=1.2,
                no_repeat_ngram_size=3,
                initial_prompt="Halo Nexo. Daftarkan suara, hapus sidik suara, rekam suara.",
            )
            kept = [
                seg.text.strip()
                for seg in segments
                if seg.text.strip() and getattr(seg, "no_speech_prob", 0.0) < 0.75
            ]
            raw = " ".join(kept)
            # Suppress full prompt echo artifacts without blocking "Halo Nexo" greetings
            prompt_echoes = {
                "halo nexo, daftarkan suara, hapus sidik suara, rekam suara.",
                "halo nexo. daftarkan suara, hapus sidik suara, rekam suara.",
                "daftarkan suara, hapus sidik suara, rekam suara.",
                "ini adalah percakapan santai bahasa indonesia dengan asisten pintar",
            }
            if raw.lower().strip() in prompt_echoes:
                return ""
            return raw

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _run_transcribe)


def create_stt_engine(provider: str = "faster-whisper", model: str = "base") -> BaseSTT:
    """Factory to create configured STT engine."""
    if provider == "mock":
        return MockSTT()
    if provider in ("faster-whisper", "whisper", "local"):
        return FasterWhisperSTT(model_size=model)
    return FasterWhisperSTT(model_size=model)
