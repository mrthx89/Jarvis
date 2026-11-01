# Voice/UI repair acceptance

## Verified on server/build host

- All 16 SVG-derived RGBA512 expressions pass `scripts/render_mascots.py --check`; black contact sheet visually inspected (not a device screenshot).
- Full Temurin JDK17 + Gradle8.7 + Android SDK34: `:app:assembleDebug :app:testDebugUnitTest` successful; 13 JVM tests, zero failures/errors (including playback failure recovery and bounded nonblocking PCM writes).
- `:app:assembleRelease` successful. Existing release configuration uses debug signing: this is an internal-test APK, not a production signing claim. Existing lint suppression was not added by this repair.
- Server pytest: 15 passed. See `server/VOICE_VERIFICATION.md` for real-provider in-process evidence and limitations.
- Backend restarted with repair. Public `wss://jarvis.yhdev.my.id/ws/appliance` probe used real Edge male Indonesian synthesis, PCM16 input, faster-whisper and Hermes. Final run: input `Halo halo.` -> transcript `Halo Halo` -> reply `Halo, Pak Yanto. Ada yang ingin dibahas?` -> 230400 bytes PCM24k mono; LISTENING -> THINKING -> SPEAKING -> IDLE, pong responsive; 7.82 seconds from connection to IDLE notification (not speaker completion).

Reproduce public test from repo root:

```
PYTHONPATH=server/src server/.venv/bin/python scripts/probe_voice.py
```

Probe artifacts/logs are under the host scratch directory, not committed. The generated input is synthetic speech, not an Android recording. The public probe waits for `tts_end` and server `IDLE`; it does not verify Android playback, hardware drain, or return to ready on a physical device.

## Still requires physical device

Install a newly built APK, allow microphone access, check portrait/black face rendering, wait for microphone-active status, say `Halo halo`, pause roughly one second, verify transcription/answer/audible male voice, then a second turn. Disconnect/reconnect Wi-Fi and verify capture recovers. Energy-only VAD is sensitive to room noise and pauses. Half-duplex intentionally suppresses mic transmission during processing/playback; this is not wake-word or barge-in support.

## Deployment caveat

The pre-existing public WebSocket has no device authentication. This repair does not add authentication or change access policy. Restrict access before exposing an agent-capable gateway beyond trusted testing; a device ID/hello is not authentication. Health/handshake alone do not prove voice readiness.
