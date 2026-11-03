"""Exercise real MP3 decoding with an offline encoded audio fixture."""
import asyncio
import io
import math
from contextlib import aclosing

from nexo_server.pipeline import tts as tts_module
from nexo_server.pipeline.tts import BaseTTS

import av
import numpy as np
import pytest
import edge_tts

from nexo_server.pipeline.tts import EdgeTTS


def mp3_tone():
    output = io.BytesIO()
    with av.open(output, mode="w", format="mp3") as container:
        stream = container.add_stream("mp3", rate=24000)
        stream.layout = "mono"
        samples = (0.4 * np.sin(2 * math.pi * 440 * np.arange(24000) / 24000)).astype("float32")
        frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="fltp", layout="mono")
        frame.sample_rate = 24000
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return output.getvalue()


@pytest.mark.asyncio
async def test_edge_mp3_is_decoded_to_pcm16_mono_24khz(monkeypatch):
    encoded = mp3_tone()
    voices = []

    class Communicate:
        def __init__(self, text, voice):
            voices.append(voice)

        async def stream(self):
            # Deliberately split MP3 packets across transport chunks.
            for start in range(0, len(encoded), 511):
                yield {"type": "audio", "data": encoded[start:start + 511]}

    monkeypatch.setattr(edge_tts, "Communicate", Communicate)
    chunks = [c async for c in EdgeTTS("id-ID-GadisNeural").synthesize_stream("Halo")]
    pcm = b"".join(chunks)
    assert 48000 <= len(pcm) <= 54000
    assert all(len(c) % 2 == 0 for c in chunks)
    assert voices == ["id-ID-GadisNeural"]
    samples = np.frombuffer(pcm, dtype="<i2").astype(float)
    spectrum = abs(np.fft.rfft(samples))
    peak = np.fft.rfftfreq(len(samples), 1 / 24000)[np.argmax(spectrum)]
    assert abs(peak - 440) < 2
    assert samples.std() > 1000


@pytest.mark.asyncio
async def test_edge_network_timeout_does_not_include_playback_backpressure(monkeypatch):
    encoded = mp3_tone()

    class Communicate:
        def __init__(self, text, voice):
            pass

        async def stream(self):
            yield {"type": "audio", "data": encoded}

    real_timeout = asyncio.timeout
    monkeypatch.setattr(edge_tts, "Communicate", Communicate)
    monkeypatch.setattr(asyncio, "timeout", lambda _: real_timeout(0.01))
    async with aclosing(EdgeTTS().synthesize_stream("offline tone")) as audio:
        first = await anext(audio)
        # A slow socket/full playback queue is not a stalled Edge network read.
        await asyncio.sleep(0.03)
        rest = [pcm async for pcm in audio]
    assert len(first) + sum(map(len, rest)) >= 48000


@pytest.mark.asyncio
async def test_prefetch_starts_next_sentence_while_previous_audio_is_being_sent():
    second_started = asyncio.Event()

    class TTS(BaseTTS):
        async def synthesize_stream(self, text, voice=""):
            if text == "second":
                second_started.set()
            yield text.encode() * 2

    async def sentences():
        yield "first"
        yield "second"

    helper = getattr(tts_module, "prefetch_tts", None)
    assert helper is not None, "Sentence TTS is still coupled to websocket consumption"

    # The old gateway's nested loops cannot advance the next sentence while send_bytes waits.
    async def sequential():
        async for sentence in sentences():
            async for pcm in TTS().synthesize_stream(sentence):
                yield sentence, pcm

    async with aclosing(sequential()) as old_stream:
        await anext(old_stream)
        await asyncio.sleep(0)
        assert not second_started.is_set()

    async with aclosing(helper(TTS(), sentences())) as stream:
        assert await anext(stream) == ("first", None)
        # Simulate a websocket send blocked while the next sentence could synthesize.
        await asyncio.wait_for(second_started.wait(), 0.2)
        assert [item async for item in stream] == [
            ("first", b"firstfirst"), ("second", None), ("second", b"secondsecond")
        ]


@pytest.mark.asyncio
async def test_prefetch_closes_blocked_provider_and_sentence_source_on_early_exit():
    closed = []
    provider_waiting = asyncio.Event()

    class TTS(BaseTTS):
        async def synthesize_stream(self, text, voice=""):
            try:
                yield bytes(2)
                provider_waiting.set()
                await asyncio.Event().wait()
            finally:
                closed.append("provider")

    async def sentences():
        try:
            yield "first"
        finally:
            closed.append("source")

    async with aclosing(tts_module.prefetch_tts(TTS(), sentences())) as stream:
        await anext(stream)
        await provider_waiting.wait()
    assert closed == ["provider", "source"]


@pytest.mark.asyncio
async def test_prefetch_preserves_large_pcm_in_bounded_ordered_messages():
    payload = bytes(range(256)) * 1500
    produced = 0

    class TTS(BaseTTS):
        async def synthesize_stream(self, text, voice=""):
            nonlocal produced
            for _ in range(3):
                produced += 1
                yield payload

    async def sentences():
        yield "first"

    async with aclosing(tts_module.prefetch_tts(TTS(), sentences(), buffer_bytes=4800)) as stream:
        assert await anext(stream) == ("first", None)
        for _ in range(10):
            await asyncio.sleep(0)
        assert produced == 1, "Producer must stop reading when the bounded buffer is full"
        chunks = [pcm async for _, pcm in stream if pcm is not None]
    assert b"".join(chunks) == payload * 3
    assert all(0 < len(c) <= 4800 and len(c) % 2 == 0 for c in chunks)


@pytest.mark.asyncio
async def test_prefetch_preserves_prior_audio_then_propagates_provider_failure():
    class TTS(BaseTTS):
        async def synthesize_stream(self, text, voice=""):
            yield bytes(2)
            raise RuntimeError("offline provider failure")

    async def sentences():
        yield "first"

    async with aclosing(tts_module.prefetch_tts(TTS(), sentences())) as stream:
        assert await anext(stream) == ("first", None)
        assert await anext(stream) == ("first", bytes(2))
        with pytest.raises(RuntimeError, match="offline provider failure"):
            await anext(stream)


def test_tts_dual_compatibility():
    """Verify legacy nexo_server tts import matches nexo_server."""
    from nexo_server.pipeline.tts import EdgeTTS as LegacyTTS
    assert LegacyTTS is not None
