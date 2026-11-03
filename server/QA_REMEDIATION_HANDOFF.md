# Backend QA remediation handoff

## Scope

Focused server-only correction of findings B1–B7 against baseline `86a032a04039433c8244a3fe6a284e79248090e8`. This is an implementation handoff, not QA approval or release permission. Existing downstream QA card `t_a6e1e693` must review the integrated revision after Android remediation. No push, deploy, production restart, Android build, credential access, private model/profile mount or real recording was performed.

## Corrections and regression evidence

- **B1, admission:** upload assembly, accepted-but-ACK-pending dispatch, active workers, terminal deliveries and external speech share the v2 busy predicate. Reservation happens synchronously before yielding. Only the admitted delivery task may enter its own nested TTS. External cancellation retains the reservation until actual TTS cleanup completes.
- **B2, mirroring:** every output generation drain synchronously notifies registered hub lifecycle listeners. The existing mirror invalidator clears pending/deferred rows and snapshots the reader high-water floor. Already-running reads are filtered against the updated floor. Tests cover prequeued, deferred, in-flight reader and cancelled mirrored speech, and preserve later legitimate finals.
- **B3, enrollment:** process-local metadata ledger keyed by authenticated device/enrollment ID survives volatile sample/lease teardown. Identity remains bound to original owner/lease. Successful publication records the result before the ACK await and clears sample embeddings/order. Duplicate begin/save/cancel returns that saved result, including after release/expiry/reconnect; cancellation before publication retains cancelled result. Only terminal metadata survives, not audio/embeddings/verifier objects. Capacity is 128/device and 512 global; terminal TTL is 120 seconds, no young-entry eviction or retry-based extension. Replay reports a past operation, never grants a lease or permission to publish.
- **B4, deadlines:** independent 5-second progress inactivity and 30-second absolute upload wall deadline. Only validated new contiguous bytes refresh inactivity; duplicate begin/ranges, invalid chunks and status do not. Chunks/commit also check monotonic deadlines before admission, so a delayed timer callback cannot admit expired PCM. Timeout pauses the session and never dispatches partial audio.
- **B5, cancellation:** unknown utterance cancellation reserves a bounded terminal tombstone before ACK. Status/reconnect/delayed commit cannot resurrect it. First valid delayed begin binds metadata without accepting audio; later changed metadata returns id_conflict. Full ledger returns capacity rather than a false cancelled ACK. TTL remains fixed from first cancellation.
- **B6, identity:** immutable task-local utterance context is inherited by asynchronous pipeline/TTS producers and attached by the serialized writer to conversation-derived frames. Actual streaming and nonstreaming gateway tests process two turns in the same epoch and verify identity on state/transcription/TTS/telemetry. External speech explicitly clears inherited utterance context.
- **B7, legacy cleanup:** legacy registration remains quarantined through provider/output cancellation cleanup. Close waits at most 1.5 seconds; unfinished tasks retain admission quarantine until their completion callbacks run. Tests hold cancellation both during close and beyond its bounded return, deny replacement v2 hello, then admit after actual cleanup.

The five independent QA probes are preserved unchanged in `tests/test_independent_qa_regressions.py`; all passed, including the real 5.2-second inactivity probe. Each B1–B7 primary regression was observed failing before its correction. Additional ACK-latency and cancellation-cleanup tests also reproduced failures before correction.

## Compatibility decisions

The backend **25-second processing bound** intentionally remains aligned to Android's absolute **30-second terminal deadline**. This is the implemented exception to the initial contract's 120-second processing row, not a policy weakening or an accidental timeout change. Processing includes acceptance-ACK latency and playback drain.

A replacement connection starts PAUSED. `utterance_status` is allowed while paused. Unknown same-instance conversation retry requires an **explicit user resume** within the original client retry TTL; no automatic resume or unknown-retry bypass is introduced. Accepted/completed/cancelled identities must never redispatch. Enrollment is never automatically replayed. This decision was coordinated with Muse on the Android remediation card.

Owner verification still precedes v2 local commands and Hermes. No threshold, pinned model/frontend, profile format, TTS prefetch/pacing or legacy frame requirements were changed.

## Validation environment and limits

All behavioral execution uses the existing ignored sandbox launcher `server/.pytest_cache/run-backend-sandbox.sh`: no network, read-only repository/toolchain, empty allowlisted environment, dropped capabilities, no-new-privileges, private scratch HOME/TMPDIR, 2 CPUs, 2 GiB memory and 128 processes. No production profile or model is mounted. Exact final SHA, full-suite count, pip check and clean-tree result are recorded in task completion metadata after commit.

The real CAM++ frontend parity test remains intentionally skipped when its pinned model is absent in the sandbox. Synthetic tests establish protocol/concurrency behavior only, not acoustic silence, outdoor transcription/biometric accuracy or Android hardware behavior. Existing leftover sandbox `nexo-outdoor-r06` was documented upstream; no approval bypass or production operation was attempted here.
