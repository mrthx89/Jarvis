"""Real voice smoke test: Edge speech -> WSS -> STT -> Hermes -> PCM.
Run with PYTHONPATH=server/src server/.venv/bin/python scripts/probe_voice.py.
Not an Android microphone/speaker or wake-word test. Uses live providers.
"""
import argparse
import asyncio
import json
import os
import inspect
import stat
from pathlib import Path
import time
import wave

import av
import numpy as np
import websockets
try:
    from nexo_server.pipeline.tts import EdgeTTS
    from nexo_server.auth import credential_origin, DEVICE_ID, TOKEN
except ImportError:
    from nexo_server.pipeline.tts import EdgeTTS
    from nexo_server.auth import credential_origin, DEVICE_ID, TOKEN

class NoRedirectConnect(websockets.connect):
    # Current asyncio client and legacy 12/13 client use different hooks.
    def process_redirect(self, exc):
        return exc

    def handle_redirect(self, uri):
        raise ValueError('Redirects are disabled for credentialed probes')


def auth_headers(args):
    path = args.credentials or os.environ.get('NEXO_PROBE_CREDENTIAL_FILE') or os.environ.get('JARVIS_PROBE_CREDENTIAL_FILE')
    if not path:
        raise ValueError('Private provisioned credential file required')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, encoding='utf-8') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 4096:
            raise ValueError('Credential file must be private and bounded')
        credential = json.loads(stream.read(4096))
    if credential.get('origin') != credential_origin(args.url):
        raise ValueError('Credential origin does not match endpoint')
    device, token = credential.get('device_id', ''), credential.get('token', '')
    if not DEVICE_ID.fullmatch(device) or not TOKEN.fullmatch(token):
        raise ValueError('Invalid provisioned credential')
    return {'Authorization': 'Bearer ' + token, 'X-Device-ID': device}, device


async def run(args):
    headers, device_id = auth_headers(args)
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
    # websockets 12 uses extra_headers; 14+ calls it additional_headers.
    header_arg = 'additional_headers' if 'additional_headers' in inspect.signature(websockets.connect).parameters else 'extra_headers'
    async with NoRedirectConnect(args.url, max_size=2**22, **{header_arg: headers}) as ws:
        await ws.send(json.dumps({'type': 'client_hello', 'device_id': device_id, 'protocol_version': '1.0',
                                 'audio_spec': {'format':'audio/pcm','sample_rate':16000,'channels':1,'bit_depth':16}}))
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
    parser.add_argument('--url', default='wss://nexo.yhdev.my.id/ws/appliance')
    parser.add_argument('--credentials', help='Path to mode-0600 provisioned JSON (or JARVIS_PROBE_CREDENTIAL_FILE)')
    parser.add_argument('--phrase', default='Halo halo.')
    parser.add_argument('--output', default='/data/home/.hermes/cache/scratch/nexo-voice-response.wav')
    parser.add_argument('--timeout', type=float, default=130)
    try:
        asyncio.run(run(parser.parse_args()))
    except Exception:
        # Network exception reprs can include request headers. Never print them.
        parser.exit(1, 'Voice probe failed; check credentials, origin and provider/network availability.\n')
