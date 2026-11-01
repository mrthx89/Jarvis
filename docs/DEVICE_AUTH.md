# Stage 1 device authentication — owner provisioning

This is **manual per-device provisioning**, not interactive/automated pairing. There is
no provisioning HTTP endpoint. Each device gets a 256-bit random bearer token; the
server registry stores only SHA-256 hashes. A device token authorizes that device to
use the voice gateway and its configured Hermes bridge. **This does not sandbox
Hermes or reduce the configured agent's privileges.** Protect the server and agent
account accordingly. Health is public and does not imply authentication/readiness.

## Migration gate (do this before deployment)

The running service/config/secrets were not modified or restarted by this change.
**Old APKs and old probes do not authenticate and will be rejected after the server
change is deployed.** Do not restart the backend just to try this code until the owner
has prepared the updated native client and provisioned credentials. No compatibility
bypass exists; an empty/missing/invalid/insecure registry denies every device.

1. Install/test the updated APK when the owner chooses to build it. Long-press the
   status text to view the actual device ID and resolved WSS origin. Do not invent
   an ID; Android ID is app/signing/user scoped and can change after reset/re-signing.
2. Independently verify the displayed WSS host+port belongs to your gateway. Remote
   Config supplies only the endpoint, never tokens. Provision outside the repository
   on the owner-controlled host, using a private directory owned by the server user:

   ```sh
   # Example only. Adapt paths and device ID; do not run against live files blindly.
   umask 077
   mkdir -p "$HOME/.config/jarvis-auth"
   chmod 700 "$HOME/.config/jarvis-auth"
   cd /path/to/Jarvis/server
   PYTHONPATH=src .venv/bin/python -m jarvis_server.auth provision \
     --registry "$HOME/.config/jarvis-auth/devices.json" \
     --device-id 'ID-SHOWN-ON-DEVICE' \
     --origin 'wss://your-gateway.example/ws/appliance' \
     --output "$HOME/.config/jarvis-auth/device-delivery.json"
   ```

   The CLI creates registry and delivery file with mode 0600; token is never printed.
   Delivery contains `device_id`, `origin`, and the **only issued raw token copy**.
   Output must not already exist. Device IDs must be 1–128 ASCII letters/digits/`_`/`-`.
   CLI serializes cooperating writers using a private `.lock` file, updates registry
   atomically, rejects duplicate IDs and never rotates a token silently.
3. Read the delivery file privately (not in shared terminals/logs/chat), enter only
   its token in the masked native dialog, and save. The app never redisplays the
   saved token. Check the dialog's ID and origin against the delivery file. Android
   encrypts token using Android Keystore AES-GCM; origin+device ID are authenticated
   as associated data. Autofill/IME learning and dialog screenshots are discouraged/
   disabled, but a malicious keyboard or compromised device remains out of scope.
   Avoid shared clipboard; erase any temporary delivery/transfer copy after setup.
4. Set the *external* deployed YAML `server.device_registry_path` to the absolute
   registry path; ensure the service user can read it and it has no group/world
   permissions. Preserve directory ownership and backups. No token goes in YAML,
   Remote Config, Gradle, APK literals, URL/query strings or logs. Use WSS with a
   valid certificate, a loopback origin behind a trusted proxy/tunnel, and protect
   upstream header/access logs. The default entrypoint trusts forwarded headers
   only from `127.0.0.1`, not every remote peer.
5. Schedule coordinated server/client deployment. Verify auth denial, valid hello,
   then a real device voice round trip separately. Python tests/static checks are
   **not** evidence of Kotlin compilation, APK installation, Keystore behavior or
   working microphone/speaker audio. None of those gates were run for this change.

## Endpoint changes, reset and recovery

- Saved credentials are bound to normalized `wss://host:port` (default port 443) and
  device ID. A different Remote Config host or port cannot receive that token; the
  service stops capture/playback and asks for setup instead. Redirect following is
  disabled. A path change on the same origin is allowed; treat the entire origin as
  trusted. Any legitimate origin migration needs owner verification and re-entry
  (prefer revoke/reissue), not automatic credential forwarding.
- HTTP 401/403 and close 1008 stop automatic reconnect. Save a valid/reissued token
  through long-press status to create a fresh connection; ordinary network outages
  still retry with backoff. Setup is a manual local operation, not remote pairing.
- “Hapus token” deletes local encrypted preferences and the Keystore alias, stops
  the old connection/capture and returns to setup required. It does **not** revoke
  the server token. Reinstall, app-data reset, missing/invalidated Keystore keys or
  signing/user identity changes require setup again; revoke obsolete IDs.

## Revoke and rotate

```sh
cd /path/to/Jarvis/server
PYTHONPATH=src .venv/bin/python -m jarvis_server.auth revoke \
  --registry "$HOME/.config/jarvis-auth/devices.json" \
  --device-id 'ID-SHOWN-ON-DEVICE'
```

The registry is reread before admission and before each incoming application frame.
New connections are denied immediately after the atomic update; active sessions
close on their **next incoming application frame**. This is not an instant kill:
an idle socket may remain open, and already-started STT/agent/TTS work can continue
until another frame/disconnect cancels the session. WebSocket transport pings alone
do not trigger registry reload. For an urgent compromise, the owner must terminate
that connection/process using their normal operations procedure. There is **no
automatic token expiry** in Stage 1; rotate/revoke explicitly. To rotate, revoke,
provision the same ID to a new delivery filename, enter the new token and delete
old delivery copies. Never copy an old hash/token to multiple IDs.

## Limits and tests

- `server.max_sessions` defaults to 4 **per process**. Use one worker or divide a
  global capacity across workers. Authenticated clients can occupy those slots;
  this is bounded admission, not a complete IP-based rate limiter. Apply connection
  rate limiting at the trusted proxy for public exposure.
- Hello deadline defaults to 5 seconds; first frame must be hello. JSON is limited
  to 4096 UTF-8 bytes and must be an object. Identity, protocol `1.0`, and complete
  PCM16/16kHz/mono specification must match before audio/VAD/providers can run.
- `python -m jarvis_server.main` enforces 64 KB transport frames and queue length 4.
  If launching `uvicorn module:app` directly, explicitly retain equivalent
  `--ws-max-size 64000 --ws-max-queue 4` settings. Application JSON/PCM limits still
  apply, but a transport cap is needed to bound allocation before application code.
- Linux/POSIX owner CLI uses `fcntl`/`O_NOFOLLOW`; registry and delivery must be
  private regular files. Protect registry directory and service identity; hashes
  of high-entropy tokens are not a substitute for filesystem access control.

Lightweight verification (no builds):

```sh
cd server
PYTHONPATH=src .venv/bin/python -m pytest tests -q \
  -o asyncio_default_fixture_loop_scope=function
```

For an **opt-in** future live gateway probe, provision a separate probe device and
keep its delivery JSON private. From repository root:

```sh
JARVIS_PROBE_CREDENTIAL_FILE=/private/probe-delivery.json \
  PYTHONPATH=server/src server/.venv/bin/python scripts/probe_voice.py \
  --url wss://your-gateway.example/ws/appliance
```

There is no raw-token CLI argument. The probe checks delivery-file origin and mode
before contacting providers and refuses redirects. Do not run it before coordinated
deployment. `server/scripts/probe_voice.py` is a separate in-process real-provider
harness with an explicitly injected test identity; it **does not test gateway auth**.
Earlier voice-probe results apply only to the pre-auth revision and are not fresh
Stage 1 live verification.
