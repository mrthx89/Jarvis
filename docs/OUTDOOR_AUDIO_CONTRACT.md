# Outdoor audio implementation contract, revision 2.0

Status: normative implementation target, NOT deployed behavior. Baseline code: `7415dcf`.
Owner: YH Dev. Implementation follows the sequential Kanban chain. This card only
adds this contract, shared fixtures, offline diagnostics and their tests. Android,
gateway, sheet and pipeline workers MUST implement these requirements, not merely
link this document. Physical acceptance is pending; phone model, Android version,
distance, orientation and noise conditions have not been supplied.

This approved plan refines AGENTS.md: local segmentation is permitted; cognition,
STT, speaker verification and TTS remain server-side. WebSocket is bidirectional,
but audio interaction stays HALF-DUPLEX in this release. No automatic interruption
or full-duplex claim. This document overrides the older plan's suggestion that a
save/dismiss might restore listening: only an explicit resume action may do that.

## 1. Baseline evidence and diagnostic loop

Observed from source, not from a phone:

- `AudioRecorderManager.kt:26,37,43-44,53-59`: 3200-byte reads, requested
  VOICE_COMMUNICATION, AEC/NS attach attempts, synchronous callback on read thread.
  That callback shares `enrollmentLock`, calculates RMS, posts UI work and calls
  `sendAudioChunk` in `NexoKioskService.kt:220-245`. This proves coupling, NOT that
  network blocked a particular recording or that DSP is the sole cause.
- `MainActivity.kt:345-359`: sheet is shown before any ownership handshake. The
  service only starts exclusion when Enroll is pressed. Sheet opening itself can
  therefore coexist with a normal turn. `NexoKioskService.kt:375-380,448-449`
  requires normal voice enabled for enrollment/start replies. This violates the
  newly required enrollment-while-muted behavior.
- Server `websocket_handler.py:208-229` rejects busy enrollment rather than acquiring
  an exclusive sheet lease. Existing enrollment ACK is not an audio-owner ACK.
  Normal VAD finalizes after a 2 s arrival gap or sample silence. v2 must not.
- Existing hello accepts only `protocol_version="1.0"`; no v2 negotiation exists.
  OkHttp send=true means locally queued, never a remote integrity ACK.

Run from repository root (no models, production server or devices required):

```sh
PYTHONPATH=server/src server/.venv/bin/python -m pytest server/tests -q -o asyncio_default_fixture_loop_scope=function
server/.venv/bin/python server/scripts/outdoor_audio_baseline.py --self-test
```

Optional reproducible host VAD smoke (public model only, ignored cache, no install):

```sh
curl --fail --location --max-time 60 https://raw.githubusercontent.com/snakers4/silero-vad/5cd7945676eb32225748052e2e6a0580e4686a08/src/silero_vad/data/silero_vad.onnx --output server/.pytest_cache/silero-v6.2.3.onnx
server/.venv/bin/python server/scripts/outdoor_audio_baseline.py --self-test --vad-model server/.pytest_cache/silero-v6.2.3.onnx
```

The normal suite creates the ignored cache directory first. Optional VAD test
skips explicitly if model is absent; it never downloads implicitly. This host
probe uses already installed numpy/onnxruntime and does not select an Android AAR.
`docs/OUTDOOR_AUDIO_BASELINE.json` is captured real command output, not predicted
performance; timings vary between runs.

Before changes: 589 Python tests passed in 23.41 s with that invocation. Running
without PYTHONPATH initially gave 4 subprocess import failures, 585 passes. The
small environment change made all four pass; no auth source change was required.
These tests provision only isolated test credentials, never the live registry.

Ranked investigations for real audio degradation, before tuning DSP:

1. Capture/callback coupling: partial-read and deliberately slow-worker tests must
   conserve sample count; failure isolates buffer ownership/backpressure.
2. Transport/assembly: compare final sample count and digest client/server. A
   mismatch falsifies the claim that only ASR is responsible.
3. Segmentation/DSP/routing: if byte integrity passes, compare consented local
   playback and manual vs auto boundaries, one source/effect change per trial.
4. STT/speaker: only after identical PCM and valid boundaries, compare text/error
   categories and holdout verification. Never lower thresholds to hide failures.

Tool results are synthetic host accounting, not evidence of intelligibility,
Android scheduling, speech VAD accuracy, SNR, thermal behavior or far-field pickup.

## 2. Ownership and local utterance lifecycle

Three independent variables, never overload one Boolean:

- `conversationIntent`: persisted normal mic ON/OFF preference. Entering a sheet
  sets it OFF (same privacy behavior as mic off), never restores its former value.
- `interactionOwner`: NONE, CONVERSATION, ACQUIRING_SHEET, SHEET, RELEASING_SHEET.
  Ephemeral and generation-fenced. Acquisition is not a persisted sleep command.
- `sleepIntent`: existing voice sleep/wake semantic, unchanged by sheets/leases.

Normal send/playback requires explicit normal intent ON, foreground/permission,
current socket generation, negotiated mode, no sheet owner/pending acquisition,
and existing half-duplex playback/drain gate. Enrollment requires foreground,
permission and a current acknowledged SHEET lease, NOT conversationIntent ON.
A control-only authenticated socket must work when the normal microphone is OFF.
One AudioRecord owner at a time, including diagnostics and enrollment.

Local utterance states: PAUSED -> ARMED -> CAPTURING -> SEALED -> UPLOADING ->
ACCEPTED -> PROCESSING -> RESPONDING -> ARMED. Manual mode uses explicit Start/
Finish; auto uses speech probability boundaries. Upload/processing do not own the
read thread. PAUSED, ERROR, CANCELLED and EXPIRED are explicit states. Publish
ARMED/ready only after actual recorder startup succeeds and all gates agree.
After a sheet, ERROR/background/unknown owner, stay PAUSED until explicit resume.

Each local capture has UUID, capture generation, purpose, start monotonic time,
source/route identity, sample counter, sealed byte count/digest and terminal reason.
Preserve all samples between its boundaries, including interior silence. STOP,
permission loss, background, route/source change and recorder error invalidate the
capture and prevent upload; release read-tail buffers using generation fences.
Manual Finish seals one utterance; repeated Finish cannot create another. Max
length seals with `duration_limit` visibly, never silently cuts and continues.

### Thread and memory contract

Read thread performs AudioRecord.read, bounded copy into preallocated owned slots,
and atomic counters/error flag only. It never waits for locks held by a socket,
DSP, UI, files, SHA hashing, resampling, VAD inference or consumer coroutine. No
unbounded Handler posts or allocations per read. Partial and odd byte reads use a
one-byte carry into the next copy; a final unmatched byte is `unaligned_pcm`, not
dropped. Negative/zero read is an explicit capture error, never synthetic silence.

Initial budgets (design limits, not measured device performance):

| Resource | Hard limit / action |
| --- | --- |
| Transport PCM | signed little-endian PCM16 mono 16000 Hz, 32000 bytes/s |
| Utterance | 320000 samples / 640000 bytes / 20 s INCLUDING pre-roll |
| Pre-roll | 6400 samples / 12800 bytes / 400 ms, overwrite ONLY while ARMED |
| Read handoff | 32 slots x 3200 bytes = 102400 bytes; consumer never owns reused slot |
| Concurrent retained utterances | one capturing and one sealed/retry; reject new Start when sealed slot occupied |
| PCM buffers per client | <= 1600000 bytes including sealed/capture slots, pre-roll, handoff and queued socket copies; VAD tensors accounted separately |
| Overflow | sticky `capture_overflow`, stop/invalidate utterance, visible error; no partial upload |
| Inference lag | <= 16 model windows; else invalidate automatic utterance with `vad_overrun` |
| Socket queued bytes | <= 131072 total application pending bytes; worker yields/retries, never read thread |

The PCM budget assumes pooled ownership transfers, NOT full-buffer copies for
hash/send/STT. Immutable sealed slot may be referenced by uploader and digest
worker without copying. Server and client consumers release references before
slot reuse. If implementation needs an extra full copy, revise the explicit
budget and tests before allocating; never quietly exceed it.

No DROP_OLDEST/DROP_LATEST for active utterances. Do not conflate overwriting idle
pre-roll with loss of captured utterance samples. Ring overwrite counter and
active-capture-loss counter must be separate; active loss is fatal and nonzero
must be visible. At most one transport upload and one server turn per device.

## 3. Negotiation, versioning and compatibility

Keep the existing authenticated WSS path and device/header identity checks. Do
not change credentials, model/profile, TLS policy or production registry. Send
an additive hello with protocol_version **1.0** so old servers can parse it:

```json
{"type":"client_hello","protocol_version":"1.0","device_id":"fixture-device","audio_spec":{"format":"audio/pcm","sample_rate":16000,"channels":1,"bit_depth":16},"capabilities":["utterance_upload_v2","exclusive_audio_v1"]}
```

New server selects the intersection and includes in server_hello:

```json
{"type":"server_hello","session_id":"fixture-session","status":"IDLE","protocol_version":"2.0","capabilities":["utterance_upload_v2","exclusive_audio_v1"],"connection_id":"00000000-0000-4000-8000-000000000010","server_instance_id":"00000000-0000-4000-8000-000000000020","conversation_paused":true,"output_epoch":0,"limits":{"max_samples":320000,"max_chunk_bytes":16384,"retry_ttl_ms":30000,"owner_lease_ms":15000}}
```

Absence of selected capabilities means LEGACY, even if some unknown version field
exists. Old clients (no offers) get unchanged server_hello, raw PCM/v1 VAD and old
controls. Do not reinterpret raw v1 audio as v2. A connection selects mode once.
New Android MUST require BOTH capabilities for outdoor capture/exclusive sheet;
otherwise show server-update-required, keep normal mic OFF and capture disabled.
No silent fallback that defeats whole-utterance/exclusion guarantees. Existing
old clients remain functional, but cannot claim the new safety guarantee. Deploy
compatible server first, then owner-built Android. No automatic deployment here.

Every new JSON control has `type`, current `connection_id`, and UUID `request_id`.
UUID fields are canonical lower-case hyphenated UUID strings. Unknown optional
JSON fields may be ignored; unknown types, bad types, oversized frames or raw
binary in v2 cause a fixed safe protocol error and close 1008. Integers exclude
booleans, floats, NaN, negatives and overflow. Maximum JSON is 4096 UTF-8 bytes.
Do not accept a client-claimed device identity in place of authenticated identity.

## 4. Whole-utterance upload v2

One upload slot per authenticated device, not per socket. Normal conversation
upload requires CONVERSATION ownership; enrollment purpose requires SHEET lease.
All application ACKs use current connection_id and echo request_id where relevant.
No v2 PCM enters EnergyVAD or arrival-gap finalization.

| Direction/type | Required fields beyond common envelope | Meaning |
| --- | --- | --- |
| C utterance_begin | utterance_id, purpose=`conversation` or `enrollment`, total_samples, sha256, end_reason, lease_id (enrollment only) | SEALED PCM only; exact size/digest known before upload |
| S utterance_ack | utterance_id, stage=`begin`, next_sample=0 | server reserved bounded assembly; client may send binary |
| C binary | fixed header below + PCM | contiguous samples only |
| S utterance_ack | utterance_id, stage=`receiving`, next_sample | progress, NOT accepted/processed |
| C utterance_commit | utterance_id, total_samples, sha256 | validate exact length, digest and purpose/owner again |
| S utterance_ack | utterance_id, stage=`accepted`, total_samples, sha256, duplicate=false/true | assembly verified and dispatch entry atomically reserved |
| S utterance_result | utterance_id, status=`completed`/`failed`/`cancelled`, code | terminal processing; no transcript in diagnostic code |
| C utterance_status | utterance_id | query ledger on reconnect/ACK loss |
| S utterance_ack | utterance_id, stage=`unknown`/`receiving`/`accepted`/`completed`/`cancelled`/`rejected`, next_sample when receiving | replay-safe status, not permission to create a second turn |
| C utterance_cancel | utterance_id, reason | invalidate assembly/dispatch, generation-fence running results |
| S utterance_ack | utterance_id, stage=`cancelled` | cancellation linearization; not rollback of prior external effects |
| S utterance_error | utterance_id, code, retryable | fixed reason; no input echo |

`total_samples`: 1..320000, byte count exactly samples*2. `sha256`: lower-case
64 hex of the final wire PCM only. Hashes are wire integrity fields, NOT routine
log fields. `end_reason`: manual_finish, vad_end or duration_limit. Enrollment
quality limits may be stricter; explicit quality rejection, never truncation.

### Binary envelope

Network header integers are BIG-endian; PCM payload samples are LITTLE-endian.
28-byte header: ASCII `NXA2` (4), UUID bytes (16), `sample_offset` uint32 (4),
`payload_bytes` uint32 (4). Header size is NOT included in payload_bytes.
Payload 2..16384 bytes, even. Offset + payload_bytes/2 <= total_samples. Frames
cannot straddle utterances. Sequence = contiguous sample offset, starting at zero.
An exact duplicate of already received byte range is ACKed without append;
conflicting overlap, gap, wrong ID, oversized/unaligned/truncated header is rejected.
No JSON-header-next-binary coupling: each binary identifies its own utterance.
Server emits receiving ACK at least every 4 frames or 250 ms, whichever first.

### ACK, deduplication and reconnect

Ledger key = (authenticated_device_id, utterance_id), bound immutable metadata
(purpose, total_samples, digest). Identity is NOT socket/session ID. Before starting
STT/Hermes, atomically transition validated assembly -> accepted with a dispatch
reservation. Duplicate begin/commit of matching accepted metadata returns accepted
with duplicate=true; never dispatch twice. Metadata conflict = id_conflict, no retry.
Keep at most 128 ledger entries/device, 512 global, plus configured authenticated
session admission. Never evict nonterminal or younger-than-TTL entries to admit a
new one; return capacity. PCM lives only until processing no longer needs it, at
most 120 s processing deadline; ledger retains metadata, not PCM, 120 s terminal TTL.

On disconnect, destroy incomplete assembly. Retain accepted dispatch/ledger in the
same server process. Reconnect gets NEW connection_id; all old socket callbacks are
fenced locally. First status-query same utterance. If accepted/completed/cancelled,
never upload it again. If unknown, same server_instance_id and within 30 s LOCAL
monotonic TTL from seal: fresh begin with SAME ID, byte zero and same PCM. No resume
of partial offsets across connections. User cancel/background discards retry.
After TTL or app/process restart, discard volatile PCM; ask user to speak again.
Never automatically replay enrollment, nor save a profile after reconnect.

Exactly-once external effects across server restart are NOT promised without a
durable transactional ledger. server_instance_id changes on server restart. On a
changed/unknown instance after uncertain commit, mark `delivery_unknown`, discard
retry and require a fresh explicit utterance. Never convert an ambiguous accepted
turn into an automatic second Hermes invocation. Do not persist recordings to make
replay work. Cancellation of Hermes cannot undo tool actions already executed;
report this limitation, suppress stale output and fence new dispatches.

### Deadlines and errors (monotonic clocks, not wall time)

| Operation | Limit / required outcome |
| --- | --- |
| hello | server 5 s, client 10 s; close without audio |
| begin ACK / commit ACK | client 5 s; status-query/reconnect, not blind new ID |
| upload inactivity | server 5 s; abort, upload_timeout, NEVER run partial PCM |
| upload wall time | server 30 s from begin; abort; local retry TTL still applies |
| processing | server 120 s; terminal failed, erase PCM, release turn |
| local sealed/retry | 30 s from seal, not refreshed by retries/ACKs |
| cancel/release ACK | client 2 s; tear down socket, remain PAUSED |

Fixed error codes include unsupported_capability, invalid_message, invalid_format,
invalid_length, sequence_gap, overlap_conflict, checksum_mismatch, id_conflict,
capacity, busy, owner_required, stale_connection, upload_timeout, delivery_unknown,
cancelled, quality_insufficient, stt_empty and processing_timeout. retryable is
false except capacity/busy/upload_timeout while original retry TTL remains valid.
Do not log PCM, transcript, tokens, complete digests or speaker embeddings.

## 5. Exclusive recording/enrollment sheet, mandatory

A sheet open request (tap, voice-triggered UI event or diagnostic capture) FIRST
atomically closes normal send/playback/chime gates, persists normal intent OFF,
fences all normal capture callbacks, clears normal pre-roll/sealed pending upload,
and enters ACQUIRING_SHEET. Stop/flush local AudioTrack and ALL SoundPool streams,
cancel pending chime callbacks, stop/release normal AudioRecord off the read thread.
Only a non-ready `acquiring` shell may be visible during this operation. No ready,
level capture or Record button until local shutdown AND server lease grant agree.

Messages below require negotiated exclusive_audio_v1 and current connection_id:

| Direction/type | Additional fields | Semantics |
| --- | --- | --- |
| C audio_owner_acquire | owner_id (sheet UUID), purpose=`enrollment`/`diagnostic`, policy=`cancel_active` | fence normal turn and request lease |
| S audio_owner_ack | owner_id, state=`granted`, lease_id, lease_ms=15000, conversation_paused=true | only after quiescent server output/dispatch boundary |
| S audio_owner_ack | owner_id, state=`denied`, code | not ready; keep normal mic OFF |
| C audio_owner_renew | owner_id, lease_id | every 5 s while sheet foreground, no capture extension beyond limits |
| S audio_owner_ack | owner_id, lease_id, state=`renewed`, lease_ms | expiry extended, current generation only |
| C audio_owner_release | owner_id, lease_id if known, reason | idempotent; acquisition can be cancelled before grant |
| S audio_owner_ack | owner_id, state=`released`, conversation_paused=true | lease/capture/save buffers invalidated, still no normal input |
| S audio_owner_ack | owner_id, lease_id, state=`expired`, conversation_paused=true | stop dedicated capture, require new explicit open |
| C conversation_resume | fresh request_id | explicit user action, no active owner, foreground/permission |
| S conversation_resumed | conversation_paused=false | client still waits for recorder startup and duplex readiness |

Server stores conversation_paused on authenticated device across reconnect within
its process. A new v2 connection starts PAUSED regardless of previous state; only
explicit resume opens normal admission. No session reconnect greeting/TTS while
paused. Lease is ephemeral per connection; disconnect invalidates it and pending
saves, but never unpauses conversation. Never use sleep/wake commands or mutate
speaker profiles to implement suspension.

### Quiescence and linearization

On acquire, server immediately closes ALL normal admission (PCM, upload commit,
external speak, Hermes mirrored voice and queued output). Increment output/turn
generation before awaits; cancel the active processing/TTS/external-speech task,
clear buffers/gap timers and queued normal output. Every producer checks the
current output generation after each await and immediately before dispatch/send.
A cancelled extraction thread may finish but cannot publish or save. If Hermes
subprocess cancellation cannot be confirmed, fence it and deny ownership: do not
permit invisible concurrent conversation work. Wait at most 1500 ms for termination
and output-worker quiescence. Failure -> denied `quiesce_timeout`; close session if
state uncertain. Acquire ACK is emitted through the serialized writer after the
quiescence barrier. If an external tool action already happened, do not claim undo.

For v2, every server state_change, cognitive, transcription, tts_start and tts_end
also carries `connection_id` and `output_epoch` (nonnegative integer), plus
utterance_id for conversation-derived output. Grant/release/resume ACKs carry the
current output_epoch; acquire and resume each advance it. Client ignores old
connection/epoch controls and gates raw TTS binary on a fresh matching tts_start.
Server serializes all text/binary output through one writer; epoch is checked at
actual write, not queue insertion. Queued old epochs are discarded with scalar
counters. No stale producer can open a new tts_start after the barrier. On the
same socket, ordered WebSocket frames plus this writer/barrier ensure raw TTS
binary from the old epoch is consumed/discarded before resume ACK. On replacement
sockets, callback generation checks reject it. Never add v2 epoch fields as a
requirement for legacy clients.

Client acquisition deadline 3000 ms total from open, includes local recorder/
playback stop confirmation, control-only socket setup and server ACK. Network slow
or local stop unfinished -> visible error, release/close, stay PAUSED. A request
can be retried by explicit open, not infinite waiting. Do not claim acoustic silence
from a boolean: AudioTrack stop/flush completion and SoundPool.stop for all tracked
streams are required; keep a 600 ms echo tail before ready. Grant can arrive during
that tail, but ready requires both. OS audio focus alone is not exclusive ownership.

Key matching: owner_id + lease_id + connection_id + local generation + expected
state. ACK for closed sheet, cancelled acquire, prior connection or prior owner
cannot enable capture or resume. Idempotent duplicate acquire with same owner and
request returns the same lease; a second owner returns busy. A release before grant
tombstones owner_id for 120 s so a delayed acquire/grant cannot resurrect it.
Unknown release is safe and ACKed released. Lease expiry uses server monotonic
clock; client conservatively stops capture if renew ACK absent 2 s after send or
local deadline reached. No network dependency may delay physical local stop.

### Dedicated capture and save

After lease + local barrier, sheet state READY (conversation muted). Dedicated
Start captures with purpose enrollment/diagnostic even if normal mic OFF. No
normal microphone restart and no chime/TTS under the lease, including enrollment
success/error prompts; use visual feedback. Finish seals dedicated utterance.
Enrollment v2 uses utterance_begin purpose=enrollment + lease_id and v2 envelope,
then verified upload. It MUST NOT dispatch STT/Hermes conversation. Do not send
legacy voiceprint_enroll_start/raw audio on a v2 connection.

Enrollment operations: `enrollment_begin` (request_id/enrollment_id/lease_id)
returns `enrollment_ack` state=collecting; `enrollment_save` additionally supplies
ordered unique sample utterance_ids and holdout_utterance_id, returns state=saved
or rejected with code; `enrollment_cancel` returns state=cancelled. All include
owner_id and current lease_id. Enrollment ID is UUID. Server binds uploaded sample
IDs to that enrollment and owner; add enrollment_id to enrollment utterance_begin.
Quality worker defines count/voiced/holdout limits separately, but profile writes
must occur only after explicit Save, active lease/generation recheck and atomic
publish. Save is one linearization point. Cancel/release accepted before publish
wins, cannot save later. If publish already won, reply saved/already_saved rather
than falsely cancelled; report to user on next explicit query. Never silently roll
back/delete a saved profile. Enrollment duplicate Save returns original result,
never overwrites with new data. Reject any new sample after save starts.

On ANY exit (close button, back, swipe, outside tap, cancel, Activity.onStop,
service teardown, route/permission change, timeout, error, reconnect): stop/fence
dedicated capture immediately, zero/release private buffers best effort, invalidate
local lease and sheet generation, cancel enrollment and release owner. Queue
release before reconnect where possible; if ACK not available close socket. Server
expiry is the backstop, not permission to keep recording locally. Remain PAUSED.
No onDismiss, onConnected, state_change(IDLE), saved reply, lease release or expired
callback may start normal AudioRecord. An explicit `Lanjut mendengarkan` action
first completes release (or a fresh paused connection), then resume ACK and actual
normal capture startup. A stale resume ACK cannot override a newly opened sheet.

## 6. VAD/source design and device budget spike

Manual mode is the deterministic fallback and must ship independently of model
availability. Auto mode MUST use speech VAD, not an RMS hard gate. Candidate:
Silero VAD ONNX on ONNX Runtime Android CPU, single inference thread. Upstream
Silero and ORT license files are MIT.[1][2] This is a licensed candidate,
not a phone-performance approval. Host spike pinned Silero tag v6.2.3, commit
`5cd7945676eb32225748052e2e6a0580e4686a08`, model SHA256
`1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`,
2327524 bytes. The actual ONNX ran on host ORT 1.31.1 using synthetic silence and
seeded noise, no torch/audio I/O. 512 new samples + 64 context gives input [1,576],
state [2,1,128]. Results in `docs/OUTDOOR_AUDIO_BASELINE.json` are host-only and
NOT Android CPU/memory/accuracy evidence. Worker must select/pin Android AAR version,
verify supported ABI and redistribute license/third-party notices before merging.

Silero current ONNX wrapper uses 512-sample windows at 16 kHz (32 ms), with state/
context carried between windows.[3] Read boundaries need not equal model windows;
accumulate exact samples on a worker, no padding mid-utterance. Pin and inspect
the chosen model wrapper rather than assuming 10/20/30 ms framing. Reset inference
state on route/capture/owner generation changes, not on every read. No torch or
Python on Android. Run local deterministic synthetic input/state-shape smoke test
where possible; it is not a speech-accuracy or Android-performance measurement.

Initial tuning hypotheses: probability start >= .60 for 3 windows, continuation
>= .35, 1000 ms non-speech to seal; pre-roll 400 ms, max 20 s. Include hysteresis,
keep interior silence and final tail; these are uncalibrated design values. Noise
and other speakers can trigger speech VAD, which is not speaker authentication.
On model init/inference failure, visibly disable auto and offer manual; never
silently fall back to energy-based segmentation.

Spike acceptance budgets, TO BE MEASURED on owner's device: p95 inference <= 10 ms
per 32 ms window, p99 <= 32 ms; incremental PSS <= 40 MiB, packaged model <= 5 MiB,
AAR/ABI download contribution recorded separately, no steady-state queue growth,
no capture loss over 10 min consented use. Log CPU time, battery/temperature trend
and throttling, establish product thresholds after baseline. No Android builds,
APK/emulator/CI by agents. Prepare test source; owner builds and measures locally.
If budget fails, ship manual and expose auto unavailable with evidence, not an
untested heavy runtime or hidden hard RMS fallback.

Default candidate source is VOICE_RECOGNITION without requested NS/AEC only for
this half-duplex experiment, NOT proven best. Compare supported UNPROCESSED and
current VOICE_COMMUNICATION with requested effects recorded. Query capability,
actual format/routed device/effect availability, requested vs enabled result and
route changes. Unsupported source falls back explicitly with diagnostic reason;
UNPROCESSED cannot guarantee no vendor DSP. Do not stack NS/AGC. One tested
resampling from native 48 kHz only if device evidence justifies it. Baseline PCM
and any STT enhancement branch remain separate; enrollment/verification share the
same pinned frontend. No production model/profile/threshold change in this card.

## 7. Privacy-safe diagnostics and physical acceptance

Default metrics are scalar aggregates: sample/byte count, sequence/capture errors,
read duration distribution, queue high-water, overflow, capture source/actual PCM,
route class, effects requested/active, clipping count, RMS/peak, VAD lag, seal/
ACK/processing latency. Distinguish sample silence from packet arrival gaps. No
absolute SNR without clean reference; no transcript/digest/device serial in logs.
Per-utterance UUIDs are ephemeral correlation only, no persistent tracking.

The included CLI processes ONLY synthetic self-test data and public fixture hex;
its optional --vad-model reads the pinned public ONNX file with a strict digest
check. It never reads microphones/user audio or contacts services. Full
wire SHA belongs in memory/integrity ACK, not telemetry. Diagnostics worker must
add explicitly consented short local playback/export, default OFF, private app
cache excluded from backup, max one 20 s clip, TTL 5 min or earlier dismiss/
background/delete. No continuous file, no hidden storage, no default upload. Label
server transfer clearly. Export requires separate explicit action with warning:
external copies cannot be forcibly erased. Memory clearing is best effort, not a
claim that managed runtimes can securely erase every copy.

Owner acceptance matrix: indoor control, outdoor wind-shielded, actual outdoor;
record phone/OS, distance/orientation, noise, source/effects, network condition.
At least 20 consented genuine utterances/condition plus noise-only and consenting
other speakers is a smoke corpus, NOT security confidence. Check local playback,
byte-identical server count/digest, manual vs auto, WER/CER, command completion,
empty STT, genuine rejection/other-speaker acceptance, end-to-answer latency and
resource trends. Do not record any user audio during agent work. Physical results
are PENDING, with no promise of zero bugs or software-only far-field success.

## 8. Shared fixtures and required implementation tests

Canonical fixtures: `docs/fixtures/outdoor_audio_v2.json`. Read the SAME file in
Python (`Path`, json) and Kotlin (Gson, resolve repository ancestor then docs/
fixtures path, or test-resource copy task with exact checksum). Do not maintain
separate manually edited copies. Binary hex is synthetic, never a voice sample.
The baseline tool validates accounting/integrity vectors; it is NOT a production
protocol implementation or an Android race test. Trace vectors are normative
inputs/expected outcomes for downstream real state-machine tests.

Android capture/network worker owns buffer/model/encoder tests; integration worker
owns service/sheet ownership tests; gateway worker owns decode/ledger/lease/writer
barrier tests; pipeline worker owns enrollment/profile publication tests. Every
worker must run applicable fixtures against its REAL implementation, not only
inspect source strings, and report Android test source as unexecuted until owner
builds. Static Python guards cannot prove Android behavior or compilation.

Required deterministic tests (virtual clocks/latches; no arbitrary sleeps):

- Partial/odd reads, reused buffer overwrite, slow VAD/network/UI, full queue,
  max duration, route change and blocked-read STOP: exact count or explicit fatal
  loss, no interior gaps, no read-thread waits on consumers.
- v1 unchanged; offered/selected capability mismatch; malformed/bool/overflow
  counters; binary golden encoding in BOTH languages; wrong/gap/conflict/digest/
  commit-before-complete; rejected upload never reaches STT or Hermes.
- Disconnect at begin/mid/commit/ACK, duplicate begin/commit/cancel/status,
  changed server instance, TTL/capacity; one dispatch max per ID within ledger.
- Open sheet while listening/recording/thinking/TTS/queued chime/external speak:
  local gate closes before show-ready; server cancels and quiesces bounded;
  paused preference, no normal PCM/voice output admitted, sleep flag unchanged.
- Open while normal mic OFF: control-only socket + lease enables dedicated
  recorder, normal remains OFF. Failed local startup never shows recording.
- Each dismiss path, release-before-grant, stale/double grant/resume/renew,
  reconnect, lease expiry, permission loss and error: release/stop, no auto resume.
- Late Hermes/TTS callbacks, in-flight chime and enqueue races check generation at
  actual output, not just UI state. Cancel/save race checks atomic publish point.
- Explicit resume only after release/resume ACK + normal recorder success. A new
  sheet wins over stale resume. Duplicate UI callbacks are idempotent.

## Sources (retrieved during this task)

- Approved full plan: `/home/hermesbot/workspace/output/nexo/NEXO_OUTDOOR_AUDIO_PLAN.md`.
- Android AudioRecord: https://developer.android.com/reference/android/media/AudioRecord
- Audio sources: https://developer.android.com/media/platform/mediarecorder
- Vendor preprocessing: https://source.android.com/docs/core/audio/implement-pre-processing
- Silero source/license: https://github.com/snakers4/silero-vad/blob/master/LICENSE
- Silero wrapper, inspect pinned version before integration:
  https://github.com/snakers4/silero-vad/blob/master/src/silero_vad/utils_vad.py
- ORT license: https://github.com/microsoft/onnxruntime/blob/main/LICENSE
- ORT Android support: https://onnxruntime.ai/docs/build/android.html

## Sources

[1] https://raw.githubusercontent.com/snakers4/silero-vad/5cd7945676eb32225748052e2e6a0580e4686a08/LICENSE
[2] https://raw.githubusercontent.com/microsoft/onnxruntime/main/LICENSE
[3] https://raw.githubusercontent.com/snakers4/silero-vad/5cd7945676eb32225748052e2e6a0580e4686a08/src/silero_vad/utils_vad.py

No new UI/assets are delivered in this card; anti-slop visual delivery gate is not
applicable. Future sheet work must resolve its mode/design guidance and run that
gate. This contract supplies interaction safety, not permission to invent visuals.
