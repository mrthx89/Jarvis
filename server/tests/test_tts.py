"""Exercise real MP3 decoding with an offline encoded audio fixture."""
import io
import math

import av
import numpy as np
import pytest
import edge_tts

from jarvis_server.pipeline.tts import EdgeTTS


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
