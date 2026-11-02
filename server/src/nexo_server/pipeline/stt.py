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

        # Digital gain normalization: boost soft/distant speech so Whisper hears clearly
        peak = float(np.max(np.abs(audio_array)))
        if peak > 0.005:
            gain = min(0.95 / peak, 8.0)  # Up to +18 dB digital gain boost
            audio_array = audio_array * gain

        def _run_transcribe():
            model = self._get_model()
            segments, _ = model.transcribe(
                audio_array,
                language=language,
                beam_size=1,
                no_speech_threshold=0.5,
                condition_on_previous_text=False,
                repetition_penalty=1.2,
                no_repeat_ngram_size=3,
                initial_prompt="Halo Nexo. Daftarkan suara, hapus sidik suara, rekam suara.",
            )
            raw = " ".join([seg.text.strip() for seg in segments if seg.text.strip()])
            # Suppress direct prompt echo artifacts
            prompt_echoes = {
                "nexo", "nexo.", "halo nexo", "halo nexo.",
                "halo nexo, daftarkan suara, hapus sidik suara, rekam suara.",
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
