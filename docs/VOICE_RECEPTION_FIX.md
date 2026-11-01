# Voice reception corrections

## Server

- Speech onset keeps the existing -35 dBFS energy threshold. Once recording has started, a lower continuation threshold (-43 dBFS) retains softer syllables instead of counting them as silence. This is hysteresis, not speaker recognition or a calibrated noise classifier. The existing 1.5 second silence window and 30 second recording bound remain.
- Empty STT no longer introduces a 600ms deaf interval. No speaker output means there is no speaker echo to suppress.
- After TTS, the server waits for its estimated playback tail plus the existing 600ms echo guard before advertising IDLE. The client independently waits for actual AudioTrack drain. Previously IDLE could appear while the server was still discarding incoming microphone frames.
- Direct speech from terminal/Telegram uses the same busy exclusion: it must not overlap an active captured/processed turn or capture its own speaker echo.

## Verification boundary

Python regression tests exercise PCM framing, softer continuing speech, silence finalization, empty-STT readiness and speaker exclusion. A synthetic Indonesian Edge-TTS -> PCM16/16kHz -> cached Faster-Whisper-base probe was also run at full and quarter amplitude. Both produced a nonempty transcript but misrecognized one phrase. This is not a real microphone, room-noise, Android background or end-to-end hardware test.

No Gradle, APK build, emulator or remote Android build was run for this correction, per owner request. Server changes can be deployed independently. Android source corrections, if any, require a separately built and installed APK before they affect a tablet.

## Device acceptance

1. Open Nexo, explicitly enable the microphone, and wait for Menyimak.
2. Say a complete sentence with quieter words in its middle. Confirm the displayed transcript is not split prematurely.
3. Wait for Nexo's answer to finish and Menyimak to return, then speak immediately. Check the first word is retained.
4. Try room silence/noise: it must not generate autonomous instructions. Noise may still trigger energy VAD and produce empty STT.
5. Press Home or swipe the task away separately from Android Settings > Force stop. Force stop must stop microphone and speaker; reopening and explicitly starting voice is required. Do not bypass this privacy behavior.
6. Explicit STOP, revoked credentials and missing microphone permission must not be undone by recovery.

If capture is still intermittent, collect device AudioRecord errors and capture-level/packet-timing diagnostics. Do not claim changing the Whisper model or hard-coding a lower onset threshold fixes an unmeasured microphone path.
