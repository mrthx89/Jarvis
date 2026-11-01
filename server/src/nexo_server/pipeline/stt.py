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

        def _run_transcribe():
            model = self._get_model()
            segments, _ = model.transcribe(
                audio_array,
                language=language,
                beam_size=1,
                initial_prompt=(
                    "Halo Nexo, asisten cerdas AI. Nexo tolong tampilkan ekspresi "
                    "wajah normal, senang, sedih, tertawa, marah, terkejut, "
                    "mengantuk, keren, optimis. Repositori Nexo, kode, sistem."
                ),
            )
            return " ".join([seg.text.strip() for seg in segments if seg.text.strip()])

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _run_transcribe)


def create_stt_engine(provider: str = "faster-whisper", model: str = "base") -> BaseSTT:
    """Factory to create configured STT engine."""
    if provider == "mock":
        return MockSTT()
    if provider in ("faster-whisper", "whisper", "local"):
        return FasterWhisperSTT(model_size=model)
    return FasterWhisperSTT(model_size=model)
