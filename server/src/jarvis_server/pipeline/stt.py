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

    def __init__(self, response_text: str = "Halo Jarvis, apa status sistem saat ini?"):
        self.response_text = response_text

    async def transcribe(self, pcm_audio: bytes, language: str = "id") -> str:
        await asyncio.sleep(0.05)  # Simulate small processing delay
        return self.response_text


def create_stt_engine(provider: str = "mock", model: str = "base") -> BaseSTT:
    """Factory to create configured STT engine."""
    if provider == "mock":
        return MockSTT()
    # Placeholder for faster-whisper or external cloud providers
    return MockSTT()
