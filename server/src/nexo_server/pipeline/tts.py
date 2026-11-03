"""Text-to-Speech abstraction and engines."""

from abc import ABC, abstractmethod
import asyncio
from contextlib import aclosing
from typing import AsyncGenerator


class BaseTTS(ABC):
    """Abstract base class for Text-To-Speech engines."""

    @abstractmethod
    async def synthesize_stream(
        self, text: str, voice: str = "id-ID-ArdiNeural"
    ) -> AsyncGenerator[bytes, None]:
        """Yield raw signed little-endian PCM16, 24 kHz, mono chunks."""
        if False:
            yield b""


async def prefetch_tts(
    tts: BaseTTS,
    sentences: AsyncGenerator[str, None],
    *,
    buffer_bytes: int = 120_000,
) -> AsyncGenerator[tuple[str, bytes | None], None]:
    """Overlap synthesis with sending; yield (sentence, None) before its PCM.

    PCM is split into <=100 ms messages. A bounded queue applies backpressure,
    never drops speech. Consumers must use aclosing when exiting early so the
    provider and sentence source are cancelled immediately.
    """
    chunk_bytes = 4800
    if buffer_bytes < chunk_bytes:
        raise ValueError("TTS buffer must hold at least 100 ms of PCM")
    queue = asyncio.Queue(maxsize=buffer_bytes // chunk_bytes)
    done = object()

    async def produce():
        try:
            async with aclosing(sentences) as source:
                async for sentence in source:
                    if not sentence.strip():
                        continue
                    await queue.put((sentence, None))
                    async with aclosing(tts.synthesize_stream(sentence)) as audio:
                        async for pcm in audio:
                            if len(pcm) % 2:
                                raise ValueError("TTS returned incomplete PCM16 sample")
                            for offset in range(0, len(pcm), chunk_bytes):
                                await queue.put((sentence, pcm[offset:offset + chunk_bytes]))
        except Exception as error:
            await queue.put(error)
        else:
            await queue.put(done)

    producer = asyncio.create_task(produce())
    try:
        while True:
            item = await queue.get()
            if item is done:
                return
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)


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


class EdgeTTS(BaseTTS):
    """Microsoft Edge Neural TTS (Indonesian natural voices)."""

    def __init__(self, default_voice: str = "id-ID-ArdiNeural"):
        self.default_voice = default_voice

    async def synthesize_stream(
        self, text: str, voice: str = ""
    ) -> AsyncGenerator[bytes, None]:
        import av
        import edge_tts

        # Edge delivers MP3, not the PCM advertised by our wire protocol.
        # A parser retains incomplete MP3 frames between transport messages.
        decoder = av.CodecContext.create("mp3", "r")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=24000)

        def pcm_chunks(frames):
            for frame in frames:
                for pcm in resampler.resample(frame):
                    yield pcm.to_ndarray().astype("<i2", copy=False).tobytes()

        try:
            from ..mascot import strip_emotional_tags
            clean_text = strip_emotional_tags(text)
        except Exception:
            clean_text = text

        communicate = edge_tts.Communicate(clean_text, voice or self.default_voice)
        prefix = bytearray()
        header_checked = False
        produced = False
        async with aclosing(communicate.stream()) as stream:
            while True:
                try:
                    # Time only the provider read: a yield may wait on playback for minutes.
                    async with asyncio.timeout(45):
                        chunk = await anext(stream)
                except StopAsyncIteration:
                    break
                if chunk.get("type") != "audio":
                    continue
                data = chunk["data"]
                if not header_checked:
                    prefix.extend(data)
                    if len(prefix) < 10:
                        continue
                    if prefix[:3] == b"ID3":
                        tag_size = 10 + sum((prefix[6 + i] & 0x7f) << (21 - 7 * i) for i in range(4))
                        if tag_size > 65536:
                            raise ValueError("Oversized MP3 ID3 header")
                        if len(prefix) < tag_size:
                            continue
                        del prefix[:tag_size]
                    data = bytes(prefix)
                    prefix.clear()
                    header_checked = True
                for packet in decoder.parse(data):
                    for pcm in pcm_chunks(decoder.decode(packet)):
                        produced = True
                        yield pcm
        for packet in decoder.parse(b""):
            for pcm in pcm_chunks(decoder.decode(packet)):
                produced = True
                yield pcm
        for pcm in pcm_chunks(decoder.decode(None)):
            produced = True
            yield pcm
        for pcm in resampler.resample(None):
            produced = True
            yield pcm.to_ndarray().astype("<i2", copy=False).tobytes()
        if not produced:
            raise RuntimeError("Edge TTS returned no decodable audio")


def create_tts_engine(provider: str = "edge-tts", voice: str = "id-ID-ArdiNeural") -> BaseTTS:
    """Factory to create configured TTS engine."""
    if provider == "mock":
        return MockTTS()
    if provider in ("edge-tts", "edge"):
        return EdgeTTS(default_voice=voice)
    return EdgeTTS(default_voice=voice)
