# Improvement scope and acceptance

## Stage 1 — repository hardening

Implement owner-provisioned per-device authentication (not automatic/QR pairing), protocol gating, connection limits, safe endpoint handling, secret-free logs, separate internal/production signing, Gradle Wrapper, manual CI, and documented migration. Production tokens belong outside Git and Remote Config. Credentials must be bound to the explicitly approved server origin.

Verification for this change is intentionally limited to static checks and lightweight Python tests at the owner's request. No Gradle invocation, APK build, emulator/device test or remote CI execution is authorized for this iteration. CI is manual-only until the owner chooses to run it. Previously built APKs predate authentication and must not be presented as artifacts of this iteration.

Deployment must be coordinated: build/install the updated client, provision its device credentials, then restart the secured backend. Do not silently restart a newly secured gateway while the owner's installed client still lacks setup. Repository updates alone do not secure an already running old process. The operator must complete the migration guide; access to the old public gateway should remain restricted meanwhile.

Signing material removed from tracking still exists in Git history. Do not delete local keys or rewrite shared history automatically. Owner rotation/release-signing decisions are required if the previously tracked key was used for production. Firebase client configuration is not an administrator credential; apply Firebase/API access restrictions separately.

## Stage 2 — reliable operation

- Real device capture/playback, reconnect and overnight soak testing.
- Speech VAD evaluation with room-noise fixtures, configurable endpointing.
- Readiness and stage latency diagnostics (no routine transcript logging).
- Per-owner/device session policy and bounded inference scheduling.
- Configuration validation and reproducible dependency locks.
- Agent privilege reduction and explicit confirmation for sensitive actions. Device authentication is not an agent sandbox and does not prove speaker identity.

## Stage 3 — companion behavior

- Local wake word, deliberate mute, night/burn-in mode.
- Measure first-audio latency before streaming agent sentences.
- Test AEC before attempting barge-in; keep half-duplex fallback.
- Complete token-registration/server-send FCM workflow.
- On-demand camera only with explicit privacy state.

These later stages are not claimed complete by Stage 1 hardening.
