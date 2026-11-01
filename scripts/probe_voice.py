"""Real voice smoke test: Edge speech -> WSS -> STT -> Hermes -> PCM.
Run with PYTHONPATH=server/src server/.venv/bin/python scripts/probe_voice.py.
Not an Android microphone/speaker or wake-word test. Uses live providers.
"""
import argparse
import asyncio
import json
from pathlib import Path
import time
import wave

import av
import numpy as np
import websockets
from jarvis_server.pipeline.tts import EdgeTTS

async def run(args):
    phrase = args.phrase
    pcm24 = b''.join([chunk async for chunk in EdgeTTS().synthesize_stream(phrase)])
    frame = av.AudioFrame.from_ndarray(np.frombuffer(pcm24, dtype='<i2').reshape(1, -1), format='s16', layout='mono')
    frame.sample_rate = 24000
    resampler = av.AudioResampler(format='s16', layout='mono', rate=16000)
    pcm16 = b''.join(f.to_ndarray().astype('<i2').tobytes() for f in resampler.resample(frame) + resampler.resample(None))
    pcm16 += b'\0' * 32000  # One second of silence; deliberately no voice_end.
    result = {'input_phrase': phrase, 'input_bytes': len(pcm16), 'states': [], 'transcriptions': [], 'pcm_bytes': 0, 'pong': False}
    audio = bytearray()
    started = time.monotonic()
    async with websockets.connect(args.url, max_size=2**22) as ws:
        await ws.send(json.dumps({'type': 'client_hello', 'device_id': 'voice-smoke-probe', 'audio_spec': {'format':'audio/pcm','sample_rate':16000,'channels':1,'bit_depth':16}}))
        hello = json.loads(await asyncio.wait_for(ws.recv(), 10))
        assert hello['type'] == 'server_hello', hello
        async def send():
            for start in range(0, len(pcm16), 3200):
                await ws.send(pcm16[start:start+3200])
                await asyncio.sleep(0.1)
            await ws.send(json.dumps({'type':'ping', 'timestamp':12345}))
        sender = asyncio.create_task(send())
        try:
            async with asyncio.timeout(args.timeout):
                while True:
                    message = await ws.recv()
                    if isinstance(message, bytes):
                        audio.extend(message)
                        continue
                    data = json.loads(message)
                    kind = data.get('type')
                    if kind == 'state_change': result['states'].append(data['state'])
                    elif kind == 'transcription': result['transcriptions'].append({'role':data['role'], 'text':data['text']})
                    elif kind == 'pong': result['pong'] = True
                    elif kind == 'tts_start':
                        assert data['format'] == 'audio/pcm' and data['sample_rate'] == 24000 and data['channels'] == 1, data
                    elif kind == 'tts_end':
                        # IDLE follows end-of-transfer; speaker drain is Android's responsibility.
                        while True:
                            final = json.loads(await ws.recv())
                            if final.get('type') == 'state_change':
                                result['states'].append(final['state'])
                                if final['state'] == 'IDLE': break
                        break
        finally:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
    result['pcm_bytes'] = len(audio)
    result['elapsed_seconds'] = round(time.monotonic() - started, 2)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output), 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000); wav.writeframes(bytes(audio))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    assert audio and len(audio) % 2 == 0, 'No valid PCM response'
    assert result['pong'], 'No pong while pipeline active'
    assert any(x['role'] == 'user' and x['text'].strip() for x in result['transcriptions']), 'No STT result'
    assert any(x['role'] == 'assistant' and x['text'].strip() for x in result['transcriptions']), 'No agent result'
    print(f'PASS: real voice pipeline; response saved to {output}')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='wss://jarvis.yhdev.my.id/ws/appliance')
    parser.add_argument('--phrase', default='Halo halo.')
    parser.add_argument('--output', default='/data/home/.hermes/cache/scratch/jarvis-voice-response.wav')
    parser.add_argument('--timeout', type=float, default=130)
    asyncio.run(run(parser.parse_args()))
