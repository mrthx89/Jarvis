# Outdoor audio contract verification

Scope: architecture/fixtures/offline baseline only. Runtime implementation remains
in downstream cards. Base commit `7415dcf`. No Android builds or production access.

## Real execution

- Initial root invocation without PYTHONPATH: 585 passed, 4 failed in 24.12 s.
  All four were CLI subprocess import errors (`nexo_server` missing), not audio
  failures. Exact corrected baseline command below: 589 passed in 23.41 s.
- Added diagnostics test first: failed because the tool was missing. Implemented
  partial-read accounting, then 1 passed. Golden duplicate-vector check failed
  because validation was missing, then 2 passed. Ten invalid vectors went red
  (missing explicit rejections), then all 12 passed after validation. CLI fixture
  report went red for missing fixture_integrity, then green. Optional real-model
  smoke went red for missing probe, then 17 focused tests passed in 0.34 s.
- Final full suite: **606 passed in 23.39 s**, including the optional real-model
  smoke (public pinned model was available in ignored cache). No skips that run.
- `git diff --check`: exit 0. New files also checked after staging.
- Citation mapping verification: `citations OK`; license/wrapper facts cited.
  Normative design numbers are proposed limits, not claims from external sources.

```sh
PYTHONPATH=server/src server/.venv/bin/python -m pytest server/tests -q -o asyncio_default_fixture_loop_scope=function
server/.venv/bin/python server/scripts/outdoor_audio_baseline.py --self-test
```

The venv is Python 3.12.3. The active agent Python has no pytest on PATH, so the
existing project interpreter was used. No package installation or dependency
change. PYTHONPATH propagates to subprocesses; pytest pythonpath alone did not.

## Synthetic evidence

`OUTDOOR_AUDIO_BASELINE.json` is actual CLI stdout. Golden fixture: 16 bytes,
8 samples, 0.5 ms, two full-scale samples; SHA256 verified. This is deliberately
not a speech/quality sample. Partial read splits include odd byte boundaries and
produce identical metrics/digest; unmatched final byte raises unaligned_pcm.

Optional Silero v6.2.3 model from pinned upstream commit:
`5cd7945676eb32225748052e2e6a0580e4686a08`, 2327524 bytes,
SHA256 `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`.
Host ORT 1.31.1, CPU provider, one intra/inter thread, 20 warmup and 200 timed
synthetic windows. Captured run p95 0.18955905 ms, p99 0.21584610 ms. These are
shared Linux host measurements, NOT Android estimates or acceptance. Input
[1,576] contains 512 new samples plus 64 context; state [2,1,128]. MIT license
retrieved at pinned Silero commit and official ORT license URL. No runtime/model
bundled or production model changed. Reproduction commands are in the contract.

## Coverage and limitations

- Python executes PCM metrics, binary golden/checksum, exact duplicate accounting,
  10 malformed upload vectors, empty/odd input, bounded fixture metadata and CLI
  no-output-file behavior. The fixture checker is NOT a production server or
  protocol parser, and must not be imported into the gateway as one.
- Canonical JSON includes 3 hello cases, 16 ownership traces, 12 exit paths and 5
  retry traces. Counts verified in code. Ownership/retry traces are test inputs,
  NOT executed runtime state machines. Future workers must test their real code.
- `OutdoorAudioFixtureTest.kt` reads the same JSON and checks header byte order,
  UUID, PCM samples and digest. It has NOT been compiled/run, per owner ban on all
  Android builds. Code inspection only; Gson/JUnit already declared in project.
- No UI was built/edited, so anti-slop visual/click-through gate is not applicable.
- Device model/OS/distance/noise corpus, routing/effect efficacy, phone VAD budget,
  capture continuity on hardware, battery/thermal trends, WER/CER and speaker
  calibration: PENDING. No audio recorded, no invented device results.
- No push, deploy, restart, live token/registry/profile modification or user audio.

## Downstream implementation handoff

Use OUTDOOR_AUDIO_CONTRACT.md and docs/fixtures/outdoor_audio_v2.json as normative.
Android capture worker: actual local buffer/manual/VAD/upload APIs, no service/UI
edits. Service/UI worker: acquire-before-ready, control-only muted enrollment,
all-exit release, no automatic resume. Gateway worker: mode selection, assembly,
idempotent ledger, lease/quiescence/output epochs and explicit resume admission.
Pipeline worker: quality/multi-sample/holdout and atomic explicit-save semantics.

A successful host suite does not close any of those implementation obligations.
No assurance of zero bugs or software-only far-field performance is made.
