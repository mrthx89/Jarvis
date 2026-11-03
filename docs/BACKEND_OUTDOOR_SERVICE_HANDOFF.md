# Backend outdoor-audio implementation handoff

## Scope and release status

Backend implementation layered on Android/service commit `f918cf047d8312130285077cd2d9cd863df58fc9`. The commit containing this document is the backend handoff revision; the task completion metadata records its exact SHA and exact-head tests. This is implementation evidence, not release approval. Independent QA task `t_a6e1e693` and its release child remain gates. No push, deployment, production service restart, Android build, CI, model download, production enrollment, or profile migration was performed.

The implemented wire authority is `ANDROID_OUTDOOR_SERVICE_HANDOFF.md`, especially lines 34–118, together with `OUTDOOR_AUDIO_CONTRACT.md`. Older design vectors are not a replacement for that implemented Android contract.

## Runtime changes

- `gateway/outdoor.py`: authenticated-device-bound protocol state, whole-utterance assembler, bounded sequence/length/checksum validation, explicit commit, acceptance ACK before asynchronous terminal result, and process-local retry deduplication. Incomplete uploads never reach providers. Client arrival gaps and internal PCM silence do not commit v2 utterances.
- `gateway/websocket_handler.py`: client hello 1.0 plus BOTH `utterance_upload_v2` and `exclusive_audio_v1` negotiates server 2.0, initially paused. Clients without both capabilities retain legacy handling. A simultaneous same-device connection cannot bypass ownership by selecting legacy mode. Explicit connection/request correlation and current output epochs accompany v2 controls/output.
- Exclusive ownership invalidates generations, cancels/drains previous processing and output before granting a lease, and clears playback state only after writers stop. Uncooperative tasks fail closed and quarantine device admission until they drain. Release, expiration, cancellation and disconnect never implicitly resume the conversation. Explicit permitted resume is required. Owner replay/tombstones and renewal/expiration races are covered.
- Gateway output and `session_hub.py` terminal queries participate in ownership: paused/uploading/busy v2 devices refuse external queries rather than queuing them for later. Tracked delivery tasks can be cancelled without cancelling a long-lived caller. Provider results are checked at effect/publication boundaries; stale results cannot publish output or mutate enrollment.
- Dedicated enrollment uses separate uploaded sample/holdout identities, exact ordered sample IDs, and explicit save/cancel. Query/delete messages are authorized against current server-side connection/owner state, not missing client owner fields. New enrollment requires 2–5 samples and one holdout. Failed quality/identity/save operations preserve the prior profile; no threshold reduction or owner-check exemption was introduced.
- `pipeline/enrollment_quality.py`: model-free PCM energy/duration/clipping/DC checks, finite normalized 192-dimensional embeddings, pairwise sample consistency, holdout compatibility, and a normalized centroid. The existing 0.65 speaker threshold is not lowered. These are conservative quality checks, NOT calibrated biometrics, reliable outdoor VAD/SNR estimation, or real-model accuracy proof.
- Outcome counters distinguish no speech, upload errors, empty STT, insufficient quality and speaker rejection. Private audio/transcripts are not added to diagnostic outcome logs. Existing v1 behavior and TTS regressions remain in the full suite.

Backend asynchronous processing is bounded at 25 seconds against the Android absolute non-extending 30-second terminal deadline. Caches and leases are bounded and process-local: there is no durable cross-process deduplication guarantee. A server restart changes instance identity; clients must not blindly replay uncertain work across instances.

## Behavioral evidence

The full Python suite before commit returned **797 passed, 1 skipped in 26.33s**. The intentional skip is `tests/test_speaker_frontend_parity.py:36`: pinned real CAM++ model not provisioned in the sandbox. Synthetic/session tests do run; they do not establish real biometric performance.

Targeted vertical RED → GREEN cycles covered capability negotiation, complete PCM/no gap segmentation, upload reservation, exclusive output suppression and drain ordering, stale provider results, lease/cancel/reconnect races, external query refusal/cancellation, playback rearm, request envelopes, enrollment quality/holdout/profile protections, and cleanup after drain failure. Existing terminal speech tests now close sessions to respect same-device admission lifetime.

Tests run with the existing repository venv in an OS-enforced Docker sandbox: no network, read-only source/toolchain, empty allowlisted environment, scratch-local HOME/TMPDIR, dropped capabilities, no-new-privileges, 2 CPUs, 2 GiB memory, 128 processes, bounded tmpfs/file sizes and foreground wall-clock timeout. Test command inside the sandbox:

```sh
PYTHONPATH=src .venv/bin/python -m pytest tests -q -p no:cacheprovider \
  --basetemp=/scratch/pytest -o asyncio_default_fixture_loop_scope=function
```

The local sandbox launcher is an ignored scratch helper at `server/.pytest_cache/run-backend-sandbox.sh`, not a shipped runtime dependency or build artifact. Exact-head rerun is recorded on the task. `git diff --check` passed. Sandboxed `.venv/bin/python -m pip check` returned `No broken requirements found.` No runtime dependency files were changed.

## Offline STT comparison

`server/scripts/compare_stt.py --self-test` validates deterministic text metric fixtures in `docs/fixtures/stt_compare.json`. `--input PATH` accepts externally supplied text hypotheses/configuration labels. Tests exercise CLI execution as well as WER/CER, empty/noise hallucinations, exact normalized commands and input validation. The utility does not run ASR, read recordings, select/download models, change configuration, or fabricate latency. Optional external latency measurements are explicitly unverified. No real-provider benchmark was run and no STT-quality improvement is claimed.

## Remaining validation and operations note

- Independent source/runtime QA must inspect the exact committed revision before release. Android compilation, physical sheet interactions, capture/playback hardware barriers, outdoor STT accuracy and real speaker-model enrollment remain unverified here.
- Existing profiles/models were not modified or mounted into the execution sandbox. No raw recordings or private model/profile artifacts are included in this commit.
- An agent-created sandbox container `nexo-outdoor-r06` was observed still running after tests. Its attempted stop was denied by headless command approval policy. This is not the production Nexo service; an authorized operator should inspect/stop this leftover sandbox. Do not change approval policy or restart production as a workaround.
