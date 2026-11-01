# Stage 1 verification boundary

Scope: owner-provisioned device auth and protocol gates, origin-bound Android credential storage, WSS endpoint validation, routine token/transcript log reduction, separate internal/production signing, tracked Gradle Wrapper, manual CI, migration documentation.

Executed locally without Gradle:

- `git diff --check`: passed.
- `PYTHONPATH=server/src server/.venv/bin/python -m pytest server/tests -q -o asyncio_default_fixture_loop_scope=function`: 47 passed.
- `sh -n android/gradlew`: passed (shell syntax only, not wrapper execution).
- Official Gradle 8.7 wrapper JAR SHA256 and distribution checksum verified/pinned; JAR checksum `cb0da6751c2b753a16ac168bb354870ebb1e162e9083f116729cec9c781156b8`.
- Read-only security reviews identified and then verified source fixes for updated WebSocket test constructors and denial fallback on older supported Starlette. Actual Starlette fallback execution was also checked in the project virtualenv.

Not executed, deliberately at the owner's request:

- Any Gradle invocation, Android compilation, APK assembly, or Android unit tests.
- Remote GitHub Actions. The new workflow is manual-only.
- Device installation, Keystore/UI/capture/playback/reconnect tests.
- Live authenticated server deployment or provider/voice round trip.

No new APK is delivered. Earlier voice-repair APKs/results do not verify this revision. The live service was not restarted and existing deployed configuration was not migrated. See DEVICE_AUTH.md before activation and BUILDING.md for owner-run build/signing commands.

Security limits: registry refresh revokes active sockets on their next application frame, not an immediate kill; no automatic token expiry or speaker identity is provided. The old signing key remains in Git history even after removal from tracking. Device auth is not an agent privilege sandbox. See SECURITY_MODEL.md and IMPROVEMENT_ROADMAP.md.
