# Voice no-response repair verification

## Runtime

Python 3.11+ is required (`asyncio.timeout`). Project metadata and root README declare this minimum; Python 3.10 is no longer supported. Verification host runs Python 3.13.

## Behavior

The gateway accepts continuous raw PCM16 little-endian mono at 16 kHz; clients
need not send `voice_end`. Energy VAD starts a turn with up to 250 ms of bounded
pre-roll. A turn ends after 800 ms of received silence, 1.2 s with no audio
frames, an explicit `voice_end`, or 30 seconds of captured PCM. Empty,
odd-length, and >64,000-byte binary frames are dropped before VAD.

**Audio is intentionally half duplex**, despite bidirectional WebSocket
transport. Audio and voice-start/end controls are dropped while processing and
during a cooldown covering the calculated 24-kHz PCM playback queue plus 600 ms
of acoustic tail. Ping/pong controls remain responsive; microphone audio is not
queued as a later turn. Clients should pause mic capture during reply playback
where possible. Energy-only VAD can still mistake loud background noise for
speech or split at long pauses; this is not semantic turn detection or AEC.

Each turn runs as a background task with a 120 s ceiling. Sends have a 5 s
ceiling; Edge synthesis has a 45 s ceiling. Empty STT results skip Hermes/TTS.
Failures emit ERROR then IDLE, ending an opened TTS stream. Disconnect cancels
and awaits processing and removes the gap timer. Hermes timeout/cancellation
kills its process group and reaps the direct child. Already-running
faster-whisper executor work cannot be forcibly stopped by asyncio cancellation;
it is local inference, not an orphan Hermes agent.

Edge audio is decoded incrementally from MP3 using PyAV, resampled to signed
little-endian PCM16, 24 kHz mono, matching `tts_start`. PyAV is an explicit
requirement (it was already installed in the existing venv). The configured
voice is now honored when no per-call override is supplied.

No endpoints or authentication behavior changed. No service restart, commit,
push, or Android modification was performed for this server work.

## Automated regression suite

Run from `server/` using the existing environment:

```sh
PYTHONPATH=src .venv/bin/python -m pytest tests -q \
  -o asyncio_default_fixture_loop_scope=function
```

Final run: **15 passed in 0.65 s**. `compileall` and `git diff --check` also
completed successfully.

Tests cover continuous speech/silence without voice_end, bounded continuous
noise, audio gaps, responsive ping while STT waits, half-duplex/cooldown drops,
pre-roll, invalid frames, empty transcription, processing timeout, disconnect,
mid-stream synthesis failure cleanup, actual MP3 encoding/decoding and frequency
verification, and real child-process timeout/cancellation cleanup. Model/provider
seams are deterministic doubles in unit tests; these are not evidence of real
STT/TTS/Hermes success. The probe below is that evidence.

## Real provider probe

```sh
PYTHONPATH=src .venv/bin/python scripts/probe_voice.py \
  --output "$TMPDIR/jarvis-voice-probe-final"
```

Executed against the configured Edge Indonesian `id-ID-ArdiNeural`, local
faster-whisper `base` on CPU, and the real `/root/.local/bin/hermes` CLI in the
isolated benign session `jarvis-voice-probe`, not the appliance session. No mock
provider was used. The probe generates audio, writes WAVs, resamples to 16 kHz,
transcribes directly, and feeds 20 ms PCM frames followed by silence through the
actual session endpointing + STT + Hermes + reply-TTS path using an in-process
capture socket (not the deployed WebSocket).

Final observed result:

- Input: `Halo halo.`
- Generated input: 89,856 PCM bytes, 24 kHz, 1.872 seconds.
- Direct STT and endpointed STT both returned `Halo Halo`.
- Hermes: `Halo, Pak Yanto. Ada yang ingin dibahas?`
- Reply audio: 230,400 PCM bytes at 24 kHz (4.8 seconds).
- States: LISTENING -> THINKING -> SPEAKING -> IDLE; exactly one `tts_end`.
- Probe `passed: true`; elapsed 15.26 seconds.
- Artifacts: `$TMPDIR/jarvis-voice-probe-final/{result.json,input-24k.wav,input-16k.wav,reply-24k.wav}`.

An earlier longer Indonesian input also completed the real path, but local
Whisper base heard `Apa kabar` as `Apakah bar`, and the 800 ms endpoint split its
long pause after `Halo Halo`. This documents actual accuracy/endpoint limits,
not an assertion of perfect Indonesian recognition.

The service has not been restarted; the parent must deploy/restart and test the
live endpoint separately. Physical Android microphone/speaker playback, acoustic
echo behavior, and Cloudflare end-to-end were **not verified** by this probe.
