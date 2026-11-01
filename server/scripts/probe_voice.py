"""Opt-in real providers probe; never substitutes mock data on failure.
Run from server/: PYTHONPATH=src .venv/bin/python scripts/probe_voice.py
Artifacts go to --output (default $TMPDIR/jarvis-voice-probe).
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import time
import wave

import av
import numpy as np

from jarvis_server.config import load_config
from jarvis_server.pipeline.stt import create_stt_engine
from jarvis_server.pipeline.tts import create_tts_engine
from jarvis_server.hermes.agent_bridge import HermesBridge
from jarvis_server.gateway.websocket_handler import ApplianceSession


class CaptureSocket:
    def __init__(self):
        self.messages = []
        self.audio = bytearray()

    async def send_text(self, value):
        self.messages.append(json.loads(value))

    async def send_bytes(self, value):
        self.audio.extend(value)


def wav(path, pcm, rate):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(pcm)


async def main(output):
    output.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    result = {"input_text": "Halo halo.", "mock": False,
              "stt_provider": cfg.stt.provider, "stt_model": cfg.stt.model,
              "tts_provider": cfg.tts.provider, "voice": cfg.tts.voice}
    start = time.monotonic()
    session = None
    try:
        tts = create_tts_engine(cfg.tts.provider, cfg.tts.voice)
        pcm24 = b"".join([chunk async for chunk in tts.synthesize_stream(result["input_text"])])
        result["input_pcm24_bytes"] = len(pcm24)
        result["input_duration_seconds"] = len(pcm24) / 48000
        wav(output / "input-24k.wav", pcm24, 24000)
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        frame = av.AudioFrame.from_ndarray(np.frombuffer(pcm24, dtype="<i2").reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = 24000
        frames = resampler.resample(frame) + resampler.resample(None)
        pcm16 = b"".join(f.to_ndarray().astype("<i2", copy=False).tobytes() for f in frames)
        wav(output / "input-16k.wav", pcm16, 16000)
        stt = create_stt_engine(cfg.stt.provider, cfg.stt.model)
        result["stt_text"] = await asyncio.wait_for(stt.transcribe(pcm16), 120)
        bridge = HermesBridge(hermes_binary=cfg.hermes.hermes_binary, session_name="jarvis-voice-probe", timeout=60)
        # Full session including endpoint detection, real STT, CLI and reply TTS.
        socket = CaptureSocket()
        # In-process provider harness, NOT a gateway authentication test.
        session = ApplianceSession(socket, "probe", stt, tts, bridge, authenticated_device_id="local-probe")
        await session.handle_text_frame(json.dumps({"type": "client_hello", "device_id": "local-probe",
            "protocol_version": "1.0", "audio_spec": {"format": "audio/pcm", "sample_rate": 16000,
            "channels": 1, "bit_depth": 16}}))
        for pos in range(0, len(pcm16), 640):
            await session.handle_binary_frame(pcm16[pos:pos + 640])
        for _ in range(50):
            await session.handle_binary_frame(bytes(640))
        if session._processing_task is None:
            raise RuntimeError("No turn endpoint detected")
        await asyncio.wait_for(session._processing_task, 150)
        result["transcriptions"] = [m for m in socket.messages if m["type"] == "transcription"]
        result["states"] = [m["state"] for m in socket.messages if m["type"] == "state_change"]
        result["reply_pcm24_bytes"] = len(socket.audio)
        result["tts_end_count"] = sum(m["type"] == "tts_end" for m in socket.messages)
        wav(output / "reply-24k.wav", bytes(socket.audio), 24000)
        result["passed"] = bool(socket.audio) and "ERROR" not in result["states"] and result["tts_end_count"] == 1
    except Exception as exc:
        result["passed"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if session:
            await session.close()
        result["elapsed_seconds"] = round(time.monotonic() - start, 2)
        (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(os.environ.get("TMPDIR", ".")) / "jarvis-voice-probe")
    args = parser.parse_args()
    asyncio.run(main(args.output))
