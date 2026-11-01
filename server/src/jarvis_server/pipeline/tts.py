"""Text-to-Speech abstraction and engines."""

from abc import ABC, abstractmethod
import asyncio
from typing import AsyncGenerator


class BaseTTS(ABC):
    """Abstract base class for Text-To-Speech engines."""

    @abstractmethod
    async def synthesize_stream(
        self, text: str, voice: str = "id-ID-ArdiNeural"
    ) -> AsyncGenerator[bytes, None]:
        """Synthesize text into audio chunks (24kHz PCM or MP3)."""
        if False:
            yield b""


class MockTTS(BaseTTS):
    """Mock TTS generating dummy audio chunks with telemetry simulation."""

    async def synthesize_stream(
        self, text: str, voice: str = "id-ID-ArdiNeural"
    ) -> AsyncGenerator[bytes, None]:
        chunk_size = 4800
        total_chunks = max(3, min(len(text) // 5, 20))
        for _ in range(total_chunks):
            await asyncio.sleep(0.08)
            yield b"\x00\x01" * (chunk_size // 2)


def create_tts_engine(provider: str = "mock", voice: str = "id-ID-ArdiNeural") -> BaseTTS:
    """Factory to create configured TTS engine."""
    if provider == "mock":
        return MockTTS()
    return MockTTS()
