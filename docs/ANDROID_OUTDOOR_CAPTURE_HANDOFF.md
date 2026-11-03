# Android outdoor capture implementation handoff

Status: capture/protocol library implementation, not an integrated Android release.
No Gradle, kotlinc, Android SDK, APK, emulator or Android CI was executed. Kotlin
and Android test source are uncompiled/unexecuted. Physical acceptance remains pending.
The service, Activity, sheet, playback manager, credentials and speaker profiles
are unchanged in this task. Do not deploy this intermediate revision by itself.

Normative behavior remains `OUTDOOR_AUDIO_CONTRACT.md`. The older plan's implicit
resume wording does not apply. Only an explicit resume action may reopen normal
listening. `nexo` and `graphify` skills are not installed in the active profile;
the prior attempt queried the existing graph read-only to identify service callers.

## Production components and ownership

| Component | API / responsibility |
| --- | --- |
| `CaptureOptions` | Default options object is VOICE_RECOGNITION, requested AEC/NS OFF. `legacyComparison()` preserves existing VOICE_COMMUNICATION comparison with requested AEC/NS ON. `AudioRecorderManager` defaults to legacyComparison for existing call sites. Pass explicit outdoor options. |
| `AudioRecorderManager` | `startRecording(): Boolean`, `finishAndJoin(timeoutMs=1500): Boolean`, `stopAndJoin(timeoutMs=1500): Boolean`, `isRecording()`, `queueHighWater()`, `diagnostics`. All lifecycle calls on a serialized lifecycle worker. Never call from UI, read, inference or callback thread. |
| `CapturePump` | AudioRecord-independent production read/copy handoff, consumer and watchdog. 32 x 3200-byte pooled slots, private reused read and consumer buffers. Sink is borrowed storage valid only during callback. Sink must copy/consume before returning, never retain. Reader only reads/copies/counters/sticky failure. |
| `LocalCapture` | `arm(purpose, auto, model): long`, `start(token, monotonicMs)`, `accept(token, borrowedBytes, count, monotonicMs)`, `finish(token, monotonicMs)`, `sealed()`, `state()`, `error()`, `cancel(reason)`, `fail(reason)`, `release(sealed)`, `metrics()`. Only one consumer calls accept. Control methods fence by generation. |
| `UtteranceBuffer.Sealed` | Immutable owned PCM until erased; UUID, purpose, endReason, sealedAt, samples(), sha256(), chunk(). Do not call sha256/chunk after release. Never log hash/PCM. `copyForTest()` is test-only and MUST NOT be used by integration. |
| `SileroVad` | Construct with verified private model path on inference/lifecycle worker; `probability(float[512])`, `reset()`, `close()`. Single inference thread, not speaker recognition. |
| `CaptureLease` | `open(connection, owner, now): token`, `localStopped(token, now)`, `grant(token, connection, owner, lease, ttl, now)`, `ready(now)`, `canCapture(token, lease, now)`, `renewDue/renewSent/renewed`, `close()`. A conservative barrier helper, not a service, hardware gate or server lease implementation. |
| `NexoWebSocketClient` | Negotiates both capabilities; `onOutdoorProtocolReady`, `onOutdoorControl`; `uploadUtterance(sealed, leaseId?, enrollmentId?)`, `cancelUtterance()`, `sendOutdoorControl(type, requestId, fields)`, `fenceOutput()`, `allowOutput(connectionId, epoch)`. Existing authorization headers and redirect policy retained. |
| `UploadSession` | SDK-free real production v2 upload state, bounded pump, integrity ACK checks, original seal TTL, status-first same-ID retry, cancellation timeout. Transport requires control/binary/queuedBytes/abort. `result(id)` releases metadata admission, never resumes hardware. |
| `PrivateAudioDiagnostic` | `retain(sealed, explicitConsent): CompletableFuture<Boolean>`, `play(explicitAction): CompletableFuture<Boolean>`, `exportWav(output, explicitConsent): Boolean`, `dismiss(): CompletableFuture<Unit>`, `close()`. Opt-in only; never feeds network. |

Read cancellation invalidates callback tokens before touching hardware. Manual
Finish intentionally does not: it stops the source and drains positive tail/queued
reads. Only seal after finishAndJoin returns true. A false join forbids allocating
another AudioRecord. Capture errors/route callbacks must immediately call
LocalCapture.fail/cancel and enqueue physical stop on the lifecycle worker; do not
join from a callback. Route invalidation during startup cannot reactivate its token.
Routing callbacks are main-thread delivered, so the callback must remain trivial.
Source changes mean stop/join, cancel old capture, construct new options/session.

`LocalCapture.arm` does not start a recorder. ARMED is local segmentation state,
not permission to display ready. Publish ready only after recorder start success,
current owner/foreground/permission/half-duplex/connection gates. Auto starts after
three >=.60 windows, continues >=.35, ends after 32 non-speech windows (1024 ms).
It keeps 400 ms pre-roll, all interior silence and tail, and seals at 320000 samples
including pre-roll. Partial/odd reads carry one byte; unmatched final byte is fatal.
These thresholds are design hypotheses, not calibrated accuracy. Crowds/other
speakers can trigger the VAD. There is no voice-owner identification on the phone.

### Minimal manual flow (service pseudocode, not drop-in UI)

1. Check explicit intent/resume ACK OR current exclusive lease, foreground,
   permission and physical playback-stop/echo-tail barrier. A dedicated enrollment
   capture does not require normal conversation intent ON.
2. Ensure prior recorder joined. Reset or recreate Silero on every capture/route/
   ownership generation, on its inference worker, never concurrently with inference.
   Call `token = local.arm("enrollment", false, null)` then `local.start(token, now)`.
3. Construct recorder with explicit CaptureOptions and AudioManager. Its consumer
   invokes `local.accept(token, data, count, elapsedRealtime())`. This callback runs
   on the consumer worker, not the read thread. Poll scalar status on a bounded UI
   timer, never post one UI event per read.
4. Start recorder on lifecycle worker. Failure invalidates local capture and stays
   PAUSED/ERROR. Do not mark recording from a requested-start Boolean.
5. Finish on lifecycle worker. If recorder.finishAndJoin() succeeds, call
   local.finish(token, now) and obtain sealed. Auto/duration seal is observed via
   local.state(); stop/join physical capture when it becomes SEALED. Never append
   another read to the sealed utterance or auto-start another turn.
6. On upload worker (not UI/read/inference), uploadUtterance(sealed, currentLease,
   currentEnrollment). SHA calculation happens here. false means not admitted;
   do not silently stream raw PCM. Show uploader.state/error and wire ACK state.
7. ACK accepted validates size/digest and erases raw PCM immediately. Release the
   matching local sealed slot afterward. Preserve utterance metadata until current
   terminal result/cancel. No normal next turn until server turn/output gates permit.
   For enrollment, do not start a second sample while the prior upload is pending.
8. Every exit first fences local/owner/output, cancels/erases capture and pending
   upload, then stops/joins hardware, cancels enrollment and releases owner. Stay
   PAUSED. No saved/released/IDLE/onConnected callback starts normal capture.

## Socket and protocol integration

Hello remains protocol_version=1.0 with both offers. The server must select
protocol_version=2.0, utterance_upload_v2 AND exclusive_audio_v1, a paused session,
canonical connection/instance UUIDs and contract limits. Otherwise microphone
capture is disabled with update-required. There is no raw v1 fallback.
`sendAudioChunk` always returns false. Legacy enrollment start/finish/cancel helpers
also return false; replace service calls with lease-bound enrollment_begin/save/
cancel and v2 utterance upload. queryVoiceprint/unenrollVoiceprint retain names and
payloads, now add connection/request envelope and use the bounded sender; backend
must preserve these management operations. This task does not delete any profile.

UploadSession retains at most one PCM retry. At most one chunk is queued per 20 ms
pump tick. Binary is NXA2 + UUID + BE sample offset/length + unchanged LE PCM16.
Application queued data is limited to 131072 bytes. begin/commit ACK deadline is
5 s; socket is cancelled then status is queried on reconnect. Retry PCM expires
30 s from seal even while disconnected. Same instance + unknown status retries
same UUID at offset zero. Changed instance => delivery_unknown, erase, no replay.
Enrollment never replays on reconnect. Accepted PCM is erased; reconnect can query
accepted metadata without reupload. Cancellation erases immediately, sends same-ID
cancel and waits at most 2 s for matched ACK, then aborts socket. `clear()` is only
for terminal disposal/teardown, not to bypass cancel/result admission. The socket
scheduler is destroyed on disconnect(); create a new client object afterward.

The service must still implement exact request/owner/lease/generation matching,
release-ACK 2 s teardown, conservative renew ACK deadline, explicit resume,
conversation intent persistence and local playback/chime stop barrier. CaptureLease
tracks acquire 3 s, echo tail 600 ms, renew every 5 s, renew ACK 2 s, lease <=15 s;
service must tick/check it even with no PCM and stop physical capture on false.
A granted ACK alone does not mean ready. Listener output starts fenced. Only after
matched explicit conversation_resumed and current owner gates call allowOutput.
TTS controls need current connection and allowed output_epoch plus valid PCM24k
format. Socket callback generations reject replacement sockets. Integration must
also generation-fence the actual AudioTrack/SoundPool operations, not just these
listener callbacks. Nothing here proves acoustic quiescence.

## Diagnostic storage, source and privacy

`CaptureDiagnostics` reports requested/selected source, explicit fallback reason,
actual format, route type/ephemeral ID and available/requested/enabled AEC/NS.
UNPROCESSED is selected only if AudioManager advertises support, otherwise explicit
VOICE_RECOGNITION fallback. This is not a guarantee about hidden vendor DSP. No
software NS, resampling, AGC, enhancement or speaker-frontend change was added.
The baseline is wire PCM. Enrollment and verification must keep the same server
frontend; no separate diagnostic processing becomes biometrics preprocessing.
`metrics()` provides sample count, clipping, normalized RMS, peak and idle pre-roll
overwrite count. Queue high-water and explicit fatal errors expose loss; do not
claim absolute SNR, far-field intelligibility or best DSP without real data.

PrivateAudioDiagnostic stores one explicitly consented diagnostic-purpose WAV,
maximum 20 s, under `context.noBackupFilesDir/audio-diagnostic` (app-private,
backup-excluded). Constructor deletes any previous-process clip; 100 ms periodic
worker removes at 5 min. Call dismiss().get on background, close, route/permission
loss, owner exit and user delete. The wrapper stops MediaPlayer before unlink.
play(true) requires an explicit local playback action and current diagnostic owner;
normal conversation/chime/TTS must remain fenced. Retain transfers PCM ownership
and erases original after writing. No continuous logging or automatic transfer.
Export is a separate explicit action on a caller-owned I/O worker with user-chosen
OutputStream; close it at the caller. UI must warn that exported copies cannot be
recalled. External storage cannot block the private TTL lock. Unlink/memory zeroing
are best effort, not forensic secure erasure. Android playback/TTL behavior remains
unexecuted; host tests verify consent, WAV bytes and monotonic deletion in core.

## VAD dependency spike and licensing

- Silero v6.2.3 commit `5cd7945676eb32225748052e2e6a0580e4686a08`, bundled
  assets/vad/silero-v6.2.3.onnx, 2327524 bytes, SHA256
  `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`.
- ONNX Runtime Android pinned `com.microsoft.onnxruntime:onnxruntime-android:1.20.0`.
  Actual Maven AAR inspected, compressed download 27867924 bytes, SHA256
  `07a8f71ef890afed8c6087a56220e6d558a492804276ee2dd7cb7f6262242027`.
  No APK was generated, so per-device packaged/compressed contribution is unknown.
- Uncompressed native library bytes from AAR: arm64-v8a 18487128,
  armeabi-v7a 13073104, x86 21031784, x86_64 21184264. Owner may select ABI after
  target device is known; this task does not guess it. AAR total entries 74400848
  uncompressed bytes; classes.jar 108718 bytes.
- The real Java adapter compiled SDK-free and ran actual host ORT 1.20.0 against
  that model, silence/seeded synthetic noise, finite probabilities and reset
  determinism. Input [1,576], state [2,1,128], 512 new + 64 context, CPU single
  inter/intra-op threads. This is NOT Android app compilation or speech accuracy.
- MIT licenses and pinned ORT v1.20.0 complete ThirdPartyNotices.txt are packaged
  in assets/licenses. See upstream URLs in OUTDOOR_AUDIO_CONTRACT.md; ORT notices
  retrieved from https://raw.githubusercontent.com/microsoft/onnxruntime/v1.20.0/ThirdPartyNotices.txt.
- Copy asset to app-private model path on model-init worker, verify digest via
  SileroVad constructor; handle failure visibly as auto unavailable, offer manual.
  Do not use RMS segmentation fallback. Android ABI loading/PSS/thermal/inference
  budgets are not measured. Manual must ship independently if auto fails budget.

## Verification evidence and remaining gates

Executed from repository root:

```sh
PYTHONPATH=server/src server/.venv/bin/python -m pytest server/tests/test_android_audio_core.py -q -o asyncio_default_fixture_loop_scope=function
PYTHONPATH=server/src server/.venv/bin/python -m pytest server/tests -q -o asyncio_default_fixture_loop_scope=function
git diff --check
```

Host tests compile only SDK-free production Java (`audio/core` plus Silero adapter)
with isolated JDK 17 `javac -Xlint:all -Werror`, then execute with assertions enabled.
No Kotlin, Android sources/toolchain or build system is compiled/run. Optional host
JDK/ORT/model absence yields explicit skip, never a fabricated pass or implicit
download. Current targeted suite: 11 passed. Full suite final run: 617 passed in 26.87 s.
Both working and staged `git diff --check` passed before commit.

Covered behavior: reused read storage, partial/odd carry, interior silence,
max-duration sealing, sealed ownership, pool overflow sticky failure, blocked read
cancel, graceful positive finish tail, slow/stuck inference stopping source,
sample-based 8192 lag budget, late generation release, manual muted enrollment
primitive, lease/echo/renew/stale grant barriers, exact shared-fixture encoder/hash,
sequence/checksum rejection, queue cap, duplicate accepted ACK, cancel/reconnect
non-resurrection, status-first same-ID zero-offset retry, restart ambiguity,
enrollment replay refusal, disconnected TTL, raw disposal and cancel deadline.

Static recovery test was updated because it asserted the old source spelling and
forbade the newly approved selectable source. Actual error classification, STOP and
generation races are now exercised against production Java, not replaced by strings.
Kotlin test source covers v2 negotiation/parser, invalid integer types, source
policy, teardown-during-ready, deprecated enrollment refusal and diagnostic consent.
All Kotlin/Android tests remain unexecuted. Existing backend integrity/lease tests
are not evidence of the future v2 gateway; downstream implements its actual ledger.

Delivery gate: no UI/assets created, visual/mobile/interaction gate not applicable.
Code gate PASS: host tests executed on production classes; comments document
ownership/protocol constraints, not decorative narration; no secrets/voice/complete
runtime hashes logged; no claims of phone performance. Integration and physical
acceptance remain mandatory before release, not blocked coding or claimed success.
