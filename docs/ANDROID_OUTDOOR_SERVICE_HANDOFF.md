---
meta:
  contentType: Reference
---

# What must be verified before releasing Android outdoor audio?

**Status: source-only handoff, NOT release ready. No overall UI PASS.**

## Scope and evidence

Plan: document the current UI controls, backend contract, source findings, and remaining delivery gates. Audience: parent reviewer and backend child `t_d226debf`. Goal: implement the matching backend and verify the integrated behavior without mistaking source checks for Android execution.

This audit inspected the working tree based on `3bc7467b74e7c9aa7b4536f26d96e48aa7c21f44`. Existing uncommitted production and test changes belong to the parent task. Line references below describe this snapshot, not a future commit. Paths beginning with `java/` are relative to `android/app/src/main/java/id/my/yhdev/nexo/`; `res/` paths are relative to `android/app/src/main/`.

Updated for bounded implementation task `t_e0861b59`: added the service-owned terminal-result deadline policy and host harness, fixed current-screen HUD targets/focus/contrast with one functional native state drawable, and added authoritative post-save profile refresh. Existing task-owned service/audio/network/sheet changes are included in the local implementation commit. No capture-parent or uploader protocol change, credentials, model/profile changes, board transitions, push, deployment, restart, Gradle, kotlinc, APK, emulator, device operation, Android CI/build, or Android clicks. The parent must independently verify the final local commit. Historical line references below remain navigation aids for the pre-fix snapshot; use symbol names and the final diff for current offsets.

R-35 exception: you prohibited Android builds and execution. The per-control walkthrough below therefore records inspected source paths, not successful interactions. Runtime, rendered layout, focus, accessibility services, and audio hardware remain unverified.

## Unresolved findings

1. **HIGH, integration blocker: current backend does not negotiate the required protocol.** `server/src/nexo_server/gateway/websocket_handler.py:388-405` accepts a `1.0` hello and emits the legacy `ServerHello`. Its model at `server/src/nexo_server/models/messages.py:35-39` has no v2 capabilities, connection identity, epoch, pause state, or limits. The handler accepts legacy controls at `websocket_handler.py:427-431` and raw binary PCM at `:433-453`. Android rejects an incompatible hello at `java/network/NexoWebSocketClient.kt:208-214`; `java/network/OutdoorProtocol.kt:30-46` requires the v2 fields below. Until child `t_d226debf` implements the protocol, neither conversation nor enrollment can complete against this backend. This is intentional fail-closed client behavior, not evidence of an integrated success.
2. **Runtime/UI verification remains outstanding.** No Android compilation, rendering, TalkBack, keyboard/D-pad traversal, font-scaling, focus-return, physical audio, or device lifecycle evidence is available under the explicit restrictions. The decorative sheet rim remains 2.78:1 and is not used to identify a control. Native theme-derived switch thumb/track and sheet button state compositing remain unverified. These limits prevent UI/runtime release approval.

## Resolved source findings in this implementation

- **Terminal processing deadline:** service-owned `ProcessingDeadline` starts a non-extending 30000ms timer on the first integrity-validated `ACCEPTED` or `COMPLETED` ACK. Both conversation and enrollment require a separate matched terminal result before that deadline; result validation rejects expired or pre-acceptance completion before adding samples/resuming. Duplicate ACKs do not extend the deadline. The 100ms service tick invokes `fail` after expiry, fencing output/capture, cancelling the pending upload, releasing dedicated ownership, clearing pending IDs/timer, and leaving conversation paused until explicit continuation. All capture invalidations clear the policy. `UploadSession` and the capture-parent implementation remain unchanged: their PCM retry lifetime is not misrepresented as a processing timeout. Handler scheduling on Android is not host-proven.
- **HUD targets/focus:** status action now resides on `pillStatus` rather than its 12sp child, with 48dp minimum width/height, explicit clickable/focusable/long-clickable semantics, a named setup accessibility action, and the current full status as accessible name. Voiceprint HUD has 48dp minimum dimensions and a synchronized text name. LED/child labels do not duplicate accessibility nodes. Start/Stop, capture mode, start/finish utterance, and copy have 48dp targets and explicit focus backgrounds. The existing idle HUD alpha reduction was removed so visible focus/boundaries remain full-opacity; sleeping transitions/hardware brightness still need runtime inspection.
- **HUD non-text contrast:** `bg_hud_control.xml` provides functional 2dp default neutral boundaries and distinct 3dp cyan focused/pressed edges; no decorative asset was added. The old 1.96:1 `bg_hud_pill` is no longer referenced by the current screen. Source ratios against every explicit adjacent fill exceed 3:1 (measurements below). Real rendering remains unverified.
- **Post-save status:** matched `saved` ACK now resets the query gate and requests a fresh authoritative `voiceprint_dialog` under the existing lease. No local enrolled count/status is invented. The existing 10-second query deadline remains fail-closed if refresh never arrives.

These close bounded source defects, not the backend integration or runtime release gates.

## Backend contract for child `t_d226debf`

### Transport, negotiation, and identity

- Use WSS. `java/config/ServerEndpointPolicy.kt:11-16` rejects non-`wss` schemes, missing hosts, userinfo, query strings, fragments, and invalid ports. `RemoteConfigManager.kt:24-39,68-94,100-115` validates cached/bundled/remote endpoints before use.
- Authentication uses `Authorization: Bearer ...` and `X-Device-ID`, not URL parameters (`NexoWebSocketClient.kt:99-102`). Redirects are disabled at `:25-28`. This audit did not access or change tokens.
- Current outbound `client_hello` still carries `protocol_version: "1.0"`, the 16 kHz mono PCM16 audio spec, device ID, and capabilities `utterance_upload_v2` and `exclusive_audio_v1` (`java/network/ProtocolModels.kt:5-18`). Do not assume the client sends a `2.0` hello. Resolve negotiation by capabilities or coordinate a future protocol change outside this documentation task.
- The server response must have `type: "server_hello"`, `protocol_version: "2.0"`, both capability strings, canonical UUID `connection_id` and `server_instance_id`, nonnegative integer `output_epoch`, and JSON boolean `conversation_paused: true`.
- Required limits: `max_samples: 320000`, `retry_ttl_ms: 30000`, `owner_lease_ms: 15000`, and even integer `max_chunk_bytes` in `2..16384`. Android enforces these exact values/ranges (`OutdoorProtocol.kt:30-46`). Server hello alone never enables microphone or output.
- UUIDs must be canonical UUID strings (`java/audio/core/UploadSession.java:189-193`). Integer fields must be JSON numbers represented as nonnegative decimal integers, not strings, booleans, or decimals (`OutdoorProtocol.kt:18-27`).
- Every non-hello, non-ping/pong server frame must include the current `connection_id` (`NexoWebSocketClient.kt:185-190`). Service control frames also require canonical UUID `request_id` (`NexoKioskService.kt:637-642`), including `utterance_result` and `utterance_error`.
- JSON control frames are limited to 4096 UTF-8 bytes. Client send admission bounds its socket queue to 131072 bytes (`NexoWebSocketClient.kt:181,377-385`).

### Acquire, renew, release, and explicit resume

Every outgoing control below includes `type`, `connection_id`, and a fresh UUID `request_id` (`NexoWebSocketClient.kt:366-374`). Echo the initiating request ID in its ACK.

| Client control | Additional fields | Required server response and effect |
| --- | --- | --- |
| `audio_owner_acquire` | UUID `owner_id`; `purpose: "enrollment"` or `"diagnostic"`; `policy: "cancel_active"` | `audio_owner_ack`, `state: "granted"`, matching owner/request/connection, UUID `lease_id`, integer `lease_ms` in `1..15000`, `conversation_paused: true`, and strictly newer `output_epoch`. Cancel old cognitive/output work before granting. |
| `audio_owner_renew` | Same `owner_id`, current `lease_id` | `audio_owner_ack`, `state: "renewed"`, matching request/owner/connection/lease, `lease_ms` in `1..15000`, and a parseable nonnegative `output_epoch`. Renew only the active lease. |
| `audio_owner_release` | `owner_id`, `reason`, plus `lease_id` when one exists | `audio_owner_ack`, `state: "released"`, matching request/owner/connection, `conversation_paused: true`, `output_epoch` greater than or equal to the current epoch. Accept release of an acquire-in-flight owner without a lease. Release must not resume conversation. |
| `conversation_resume` | No owner/lease fields | `conversation_resumed`, matching connection/request, `conversation_paused: false`, and strictly newer `output_epoch`. This is the only server permission to resume normal capture. |

Source: `NexoKioskService.kt:205-215,381-431,637-667,794-801`; `java/audio/core/AudioOwnership.java:50-84,94-111`. Renew ACK parsing requires an epoch, but current ownership renewal does not compare that epoch. Do not treat this omission as permission to resume output. Denied/expired owner ACKs fail the session at `NexoKioskService.kt:660`.

Acquire has a 3-second local deadline. Release confirmation has a 2-second deadline. Resume has a 3-second deadline (`AudioOwnership.java:43-46,65-74`; `NexoKioskService.kt:789-792`). The lease barrier controls renewal timing; never wait until expiry to respond.

Readiness additionally requires confirmed local recorder, player, earcon, and diagnostic shutdown (`NexoKioskService.kt:344-379`). A server grant alone is insufficient. Release completion requires both local shutdown and the matched server ACK (`:70-75,392-428`). Dismiss, cancel, background, error, endpoint/credential change, STOP, and service teardown must remain fail-closed. Only explicit Resume requests continuation.

### Profile query and deletion

Under dedicated ownership, Android sends `voiceprint_query` and `voiceprint_unenroll` with connection/request identity. These legacy-shaped controls do **not** carry owner/lease fields (`NexoKioskService.kt:433-455`; `NexoWebSocketClient.kt:307-331`). The backend must bind them to the authenticated connection and active owner server-side.

Reply with `voiceprint_dialog`, current `connection_id`, echoed `request_id`, explicit boolean `available`, and `is_enrolled`. Optional fields are `action`, `message`, `success`, and `metadata` (`ProtocolModels.kt:67-89`). Unknown availability remains loading, not enrolled/open. The service accepts only the outstanding query/delete request while its dedicated lease remains valid. Query and delete each have 10-second deadlines. Do not send an unsolicited dialog expecting it to bypass these checks.

### Enrollment begin and policy ACK

`enrollment_begin` includes UUID `owner_id`, `lease_id`, and a new `enrollment_id`, plus the common envelope. It does not immediately start capture. Within 10 seconds, return:

```text
type: enrollment_ack
connection_id: current connection UUID
request_id: enrollment_begin request UUID
owner_id: active owner UUID
lease_id: active lease UUID
enrollment_id: supplied enrollment UUID
state: collecting
required_samples: integer 2..5
required_holdouts: integer 1
```

The policy fields are mandatory. Missing, string-valued, fractional, or out-of-range fields fail closed (`NexoKioskService.kt:668-679`). No RMS threshold, timer, or input meter substitutes for server quality policy. The first action requests policy; a subsequent action records each utterance. The client collects exactly `required_samples` sample IDs followed by one distinct holdout ID.

### Sealed sample uploads, integrity ACK, and terminal result

Source: `NexoKioskService.kt:585-635,688-709`; `UploadSession.java:56-126,128-150,195-203`.

1. Android seals one bounded utterance locally before sending. Input is PCM16 little-endian, 16 kHz, mono, at most 320000 samples (20 seconds).
2. `utterance_begin` includes common identity, UUID `utterance_id`, `purpose: "enrollment"`, `end_reason`, `total_samples`, `sha256`, `lease_id`, and `enrollment_id`. It does not include `owner_id`; validate that association from the lease/connection.
3. Reply `utterance_ack` with matching connection, begin `request_id`, `utterance_id`, `stage: "begin"`, and `next_sample: 0`. Upload waits for this ACK.
4. Binary chunks use the 28-byte NXA2 header: big-endian magic `0x4e584132`, 16 UUID bytes, big-endian sample offset, big-endian payload-byte count, then little-endian PCM16. Payload is even-sized and within negotiated `max_chunk_bytes`. Offset units are samples, not bytes.
5. Android sends `utterance_commit` with a new `request_id`, the same `utterance_id`, `total_samples`, and `sha256`. It does not repeat purpose/lease/enrollment fields. Keep the begin association server-side.
6. Reply `utterance_ack`, `stage: "accepted"` (or supported `"completed"`), matching commit request/utterance/connection, exact `total_samples`, and exact SHA-256. `receiving` progress ACKs may supply monotonic `next_sample` no greater than bytes already sent, measured in samples. Queue admission is not integrity acceptance.
7. Send a separate `utterance_result` with current connection, UUID `request_id`, the **same uploaded `utterance_id`**, and `status: "completed"` only after processing succeeds. The service appends this utterance ID to its ordered sample list. A completed ACK alone does not append a sample. A failed result needs a non-completed status and an actionable `code`; `utterance_error` likewise needs the matching utterance and request identity.
8. Never return a new sample/result ID in place of the upload UUID. Save uses these exact ordered utterance IDs. Never route enrollment samples through conversation STT/LLM/TTS.

The service validates a terminal result by pending utterance ID and connection, not an exact pending result request ID. Still provide a canonical request UUID. Send integrity acceptance before the separate terminal result. The service's `ProcessingDeadline` requires terminal processing within 30000ms of its first validated integrity ACK; duplicate ACKs do not renew this bound. `stage: "completed"` is still only integrity acceptance and also arms this timer. A result before acceptance or at/after expiry fails closed. Missing results trigger cancellation/release and explicit-retry feedback, never sample credit or automatic listening. This is an added client-side safety bound, not a change to any frame name or backend API.

`utterance_cancel` uses request/connection/utterance identity and `reason: "cancelled"`; reply with matched `utterance_ack`, `stage: "cancelled"`, before the 2-second cancellation timeout. The uploader also supports `utterance_status` with `unknown`, `accepted`, or `completed` responses, but the service closes on connection failure. Do not promise automatic enrollment retry or raw-PCM fallback.

### Save, cancel, and publication

- `enrollment_save` includes common envelope plus `owner_id`, `lease_id`, `enrollment_id`, `sample_utterance_ids` (ordered UUID array of exactly the negotiated sample count), and singular `holdout_utterance_id` (the last separately uploaded UUID). No PCM, embeddings, inferred quality score, or duration estimate substitutes for these fields (`NexoKioskService.kt:476-482`).
- Validate unique IDs, same authenticated connection/enrollment association, accepted integrity, completed processing, sample quality/consistency, and a distinct holdout. Publish atomically only after server validation. Never overwrite the old profile on partial upload, failed quality, cancel, disconnect, or expiry.
- Within 30 seconds, reply `enrollment_ack`, `state: "saved"`, echoing connection, save `request_id`, owner, lease, and enrollment IDs. Only that matched ACK produces `SAVED` (`:668-685`). A generic dialog or upload ACK is not save success.
- On rejection, use `state: "rejected"` and a concrete `code`; `state: "cancelled"` is also handled as failure, not save success.
- Panel close sends `enrollment_cancel` with owner/lease/enrollment identity when an enrollment ID exists, then releases ownership (`:416-425`). Make cancellation idempotent, including when save already completed. It must not erase an already published profile. Conversation stays paused after both saved and cancelled outcomes.
- A diagnostic lease is local-only. The backend still owns pause/lease controls, but must receive no diagnostic audio upload. `UploadSession.java:59` explicitly rejects diagnostic uploads.

### Output epoch controls

Tag `state_change`, `cognitive`, `transcription`, `tts_start`, `tts_end`, and telemetry with current connection and epoch. Old epochs and fenced output are discarded (`NexoWebSocketClient.kt:185-203`). TTS requires `format: "audio/pcm"`, `sample_rate: 24000`, `channels: 1`, `bit_depth: 16`. Send binary playback only inside a valid TTS envelope. Binary output has no separate epoch header; WebSocket ordering and the accepted envelope bind it to the epoch.

The client stops capture before accepting response audio and waits up to 1600 ms for that barrier. Timeout explicitly fails the turn (`NexoKioskService.kt:274-326`). Local playback drain, not `tts_end` alone, gates the next capture. Source fences are not proof that a specific device releases hardware promptly.

## Per-control source walkthrough (not click-through evidence)

### Main screen

| Control | Inspected source behavior | Remaining uncertainty |
| --- | --- | --- |
| Status pill, tap | `pillStatus.setOnClickListener` displays the current full message in a Toast; its content description follows status. Whole parent is focusable/clickable, minimum 48dp in both dimensions, explicit default/focused/pressed drawable. | TalkBack announcement, actual target bounds and keyboard activation not exercised. |
| Status pill, long press / accessibility setup action | `pillStatus.setOnLongClickListener` and the named `ACTION_LONG_CLICK` open the existing credential setup only after a valid endpoint exists. Tooltip names device setup. Save/reset delegates to existing credential storage and service refresh; no credentials entered or storage changed. | Accessibility action menu, keyboard long-press, dialog focus/IME not executed. |
| Voiceprint HUD | Existing click requests service ownership before constructing the sheet and refuses duplicate/background opens. Parent now has minimum 48dp dimensions, explicit focus drawable, and text-synchronized accessible name; redundant child/LED accessibility nodes excluded. | Actual modal presentation, failed-open feedback, focus transfer. |
| Start microphone / Stop | `:299-303` calls service start/stop. Service requires foreground permission and explicit resume ACK; STOP fences capture/output (`NexoKioskService.kt:198-236`). | Permission dialogs, physical mic stop, foreground notification behavior. |
| Automatic capture switch | `MainActivity.kt:307-313,406-412` calls `setAutomaticCapture`, reverts rejected changes, and reports a Toast. Service rejects unavailable VAD and active capture/upload (`NexoKioskService.kt:192-196`). | Switch tint in DayNight variants, mode switching and speech boundaries on hardware. |
| Start manual utterance | `MainActivity.kt:314-316,597` enables for voice-on `Siap` in manual mode; service checks ownership and capture conditions (`NexoKioskService.kt:509-521`). | Enabled-state timing, speech start, user perception. Explicit resume currently starts the first manual capture itself (`:723-740`). |
| Finish utterance | `MainActivity.kt:317-319,598` delegates to finish/join/seal/upload; rejection reports text. | Truncation, tail capture, duration limits and route change on hardware. |
| Copy last reply | `:323-330,615-618` enables only for a nonblank final assistant reply and copies only after explicit action. XML declares a 48dp image-button and accessible name (`activity_main.xml:210-222`). | Clipboard behavior and announcements; disabled tint. |
| Subtitle readout | `MainActivity.kt:320-321,580-583,607-619` scrolls text and preserves the last complete copyable answer. | Six-line readability, long text, screen-reader updates. |
| Wake touch / lifecycle exits | `:161-167,477-485,646-662` consumes the first sleeping touch to wake, pauses service before dismissing on background, and unbinds on destroy. | Wake ergonomics, rotation/process death/OEM behavior. |

All seven current-screen actions listed above (`pillStatus`, `pillVoiceprint`, `toggleVoice`, `captureMode`, `startUtterance`, `finishUtterance`, `copyReply`) now declare explicit keyboard focus and the same measured native state background. Start/Stop, mode switch, start/finish, and copy keep their existing native control roles and visible/accessibility labels. Source order supplies default traversal; no Tab/D-pad traversal or click was executed. Disabled controls remain native-disabled (copy also uses the existing alpha change); disabled-state rendering is not certified.

### Recording sheet and confirmations

| Control | Inspected source behavior | Remaining uncertainty |
| --- | --- | --- |
| Start next sample | `VoiceprintManagementSheet.kt:62,114-117`; service begin/policy then capture at `NexoKioskService.kt:456-466`. Disabled until session readiness and explicit availability. | Real server policy, sample quality and enabled rendering. |
| Finish recording | Sheet `:63,118-119` appears only during `RECORDING`; service joins capture before seal/upload at `:585-602`. | Physical recording completion and audio integrity. |
| Save profile | Sheet `:64,120-121` appears after samples plus holdout; service enforces exact count and no pending upload at `:476-482`. `SAVED` requires matched server ACK. | Real publication, rejected quality and lost-result recovery. |
| Cancel recording | Sheet `:65,131-132` releases ownership, disabled while releasing. Activity delegates at `MainActivity.kt:381`. It does not resume conversation. | Closed-state shell presentation and cancellation timing. |
| Resume listening | Sheet `:67,122-123` requests release/resume then dismisses. Activity's flag prevents dismissal from cancelling explicit resume (`MainActivity.kt:382,387-390`). Saved state permits this control despite session `ready == false`. | Matched release, recorder readiness and focus return on device. |
| Delete voiceprint | Sheet `:66,124-125,152-158` requires an enrolled profile and ready state. Confirmation Cancel changes nothing; confirm calls service deletion. Service checks lease and matches reply (`NexoKioskService.kt:438-454`). | Confirmation semantics, failed deletion message and profile refresh. |
| Open private diagnostic | Sheet `:68-72,126-127` shows explicit consent. Negative action does nothing; positive closes current owner. Activity opens diagnostic ownership only after `CLOSED` (`MainActivity.kt:384,395-402`). | Modal focus, release-to-acquire transition and consent comprehension. |
| Record private clip | Same start control delegates by diagnostic session at `MainActivity.kt:375-377`; no upload path (`NexoKioskService.kt:604-619`). | Local recording, storage and hardware routing. |
| Play private clip | Sheet `:74,128-130`; service requires valid lease, ready clip, and unexpired TTL (`NexoKioskService.kt:489-499`). | Sound, completion timing, volume and device routing. |
| Delete private clip | Sheet `:75,128-130`; service dismisses diagnostic data then publishes ready (`:501-507`). Diagnostic worker independently deletes at TTL (`java/audio/PrivateAudioDiagnostic.kt:27-33,79-87`). | Filesystem deletion and best-effort memory cleanup under OS faults. |
| Close / Back / swipe / outside dismissal | Sheet close `:76`; all dismissal runs `:91` and activity cleanup at `MainActivity.kt:387-390`. No implicit resume. Material default dismissal is relied upon; Escape/Back was not exercised. | Actual gestures, focus restoration, and hardware quiescence. |
| Input level | Sheet `:107-109,135` shows only during recording and resets otherwise. Feedback is a polite live region (`sheet_voiceprint_management.xml:90-106`). | Meter responsiveness and spoken announcements. Level is not quality/identity evidence. |

The sheet uses `NestedScrollView`, wrapping text, 48dp minimum button heights, system-bar/cutout insets, expanded presentation, and a 560dp maximum width (`sheet_voiceprint_management.xml:2-13,131-250`; `VoiceprintManagementSheet.kt:77-89`). These are source mitigations, not rendered layout proof. The main screen is not scrollable and adds vertically stacked capture controls; short screens and enlarged fonts need inspection before release.

## Identity and purpose record

Existing direction retained: dark/cyan Nexo appliance, **ENERGY 1 / RHYTHM 1 / MOTION 1**. No redesign or decorative assets. One native state drawable, `bg_hud_control`, exists solely to identify actionable boundaries and keyboard/D-pad focus/press states.

- Dark surfaces preserve the appliance's existing display identity; cyan marks actions rather than a new decorative palette.
- The central existing mascot remains the intended focal point; its available height with added controls is unverified.
- Predictable vertical controls separate conversation from enrollment/private recording ownership.
- Native sans-serif text and wrapping buttons prioritize readable status and explicit actions. Existing small monospace console labeling identifies the reply readout, not a new headline style.
- Sheet spacing groups status, recording guidance, primary recording actions, and exits; the scroll container accommodates content growth in source.
- Existing rounded sheet corners distinguish a modal from the HUD. No card grid or illustration was introduced.
- Motion is limited in the intended direction, but the existing visualizer, pixel shift, dimming, and transitions require a reduced-motion/accessibility review. `MainActivity.kt:69-113,573-576` contains motion; no runtime motion compliance is claimed.

## Executed source-only verification

### XML and source wiring

Parsed every XML file beneath `android/app/src/main` with Python `xml.etree.ElementTree`: **26 files parsed successfully** after adding the functional state drawable. Executed from a scratch-only Python probe, not a repository source-rewriting helper. This checks XML well-formedness, not Android resource linking or rendering.

Reproduction from repository root, without writing a helper file:

```sh
printf '%s\n' 'from pathlib import Path' 'import xml.etree.ElementTree as E' 'fs=sorted(Path("android/app/src/main").rglob("*.xml"))' 'for p in fs: E.parse(p)' 'print("XML_PARSE:",len(fs),"files parsed successfully")' | python3 -
```

Source-first RED to GREEN evidence for the bounded fixes:

- Deadline wiring test failed on missing service policy. The SDK-free policy harness initially could not compile because the class was absent; after a minimal API skeleton it ran and failed its acceptance assertion. Implemented real policy behavior, then both deadline tests passed (**2 passed in 1.85s**).
- HUD source tests first failed on the wrap-content status target and missing functional state drawable. After the target/focus/contrast implementation, the focused UI/service/audio suite passed (**31 passed in 4.23s** at that point).
- Post-save refresh test failed because the query reset/request were missing; implemented an authoritative re-query without synthesizing enrollment state, then included it in the passing full suite.

Executed full verification command:

```sh
PYTHONPATH=server/src server/.venv/bin/python -m pytest server/tests -q -o asyncio_default_fixture_loop_scope=function
```

Result: **635 passed in 27.60s**, repeated on the final implementation source as **635 passed in 27.80s**, no skips or warnings reported. Both `git diff --check` and the explicit-path staged `git diff --cached --check` returned exit 0. Added-line scans found zero hardcoded-secret, shell-injection, eval/exec, unsafe-deserialization, or SQL-formatting candidates. Five new cases supplement the previously reported 630: processing policy harness, terminal-deadline service wiring, HUD target wiring, HUD numerical contrast, and post-save authoritative refresh.

The full suite compiles SDK-free production Java with the cached JDK (`javac -Xlint:all -Werror`), runs Java assertions through the host harnesses, and runs Python/backend tests plus Android source-string/XML checks. The cached pinned ONNX/Silero host adapter case also ran without skip. The processing harness exercises pre-acceptance rejection, wrong identity, exact 30-second boundary, non-extending duplicate ACK, timely completion, duplicate result rejection, clear/replace, and clock regression. This is real production-policy host execution, NOT Kotlin/service execution, Android compilation, scheduler/race proof, rendered UI, or hardware audio evidence.

`execute_code` and `python3 -c` probes were blocked by unattended-tool policy and did not execute. Scratch-file XML parsing, pytest and installed contrast script succeeded without configuration changes.

### Contrast script results

Ran the installed antislop-human script at `/home/hermesbot/.hermes/profiles/muse/skills/antislop-human/contrast-check.py` using `python3 SCRIPT '#foreground' '#background'`. Checked explicit colors in the edited native UI and existing supporting surfaces. Ratios below are measured script output, not visual estimates.

| Foreground / background | Ratio | Scope |
| --- | --- | --- |
| `#F7F8F8` / `#101722` | 16.90:1 | Sheet primary text, base panel |
| `#B0B7C2` / `#101722` | 8.91:1 | Sheet secondary text/stroke, base panel |
| `#FFB4AB` / `#101722` | 10.59:1 | Delete text/stroke, base panel |
| `#79DCE8` / `#101722` | 11.32:1 | Accent, base panel |
| `#F7F8F8` / `#1A2535` | 14.51:1 | Sheet gradient brightest stop |
| `#B0B7C2` / `#1A2535` | 7.65:1 | Secondary text, brightest stop |
| `#FFB4AB` / `#1A2535` | 9.09:1 | Delete text/stroke, brightest stop |
| `#79DCE8` / `#1A2535` | 9.72:1 | Accent, brightest stop |
| `#F7F8F8` / `#0A0F17` | 18.05:1 | Sheet gradient darkest stop |
| `#B0B7C2` / `#0A0F17` | 9.51:1 | Secondary text, darkest stop |
| `#FFB4AB` / `#0A0F17` | 11.31:1 | Delete text/stroke, darkest stop |
| `#79DCE8` / `#0A0F17` | 12.08:1 | Accent, darkest stop |
| `#0B1017` / `#79DCE8` | 12.01:1 | Explicit primary recording-button text/fill |
| `#E0E6ED` / `#000000` | 16.71:1 | Main text/background |
| `#00E5FF` / `#000000` | 13.65:1 | Main accent/background |
| `#E0E6ED` / `#2A394E` | 9.32:1 | HUD text, brightest stop |
| `#E0E6ED` / `#1E2838` | 11.80:1 | Console text, brightest stop |
| `#B0B7C2` / `#141B26` | 8.56:1 | New default 2dp HUD control edge / fill |
| `#B0B7C2` / `#1E2838` | 7.34:1 | Default edge / brightest console adjacent surface |
| `#B0B7C2` / `#000000` | 10.40:1 | Default edge / main adjacent surface |
| `#00E5FF` / `#141B26` | 11.24:1 | Focused 3dp edge / fill |
| `#00E5FF` / `#2A394E` | 7.61:1 | Pressed 3dp edge / fill (worst explicit focused/pressed fill) |
| `#00E5FF` / `#1E2838` | 9.64:1 | Focus edge / console adjacent surface |
| `#00E5FF` / `#000000` | 13.65:1 | Focus edge / main adjacent surface |
| `#4A6A94` / `#1A2535` | 2.78:1 | Decorative sheet rim, below 3:1 |

Explicit active text pairs above exceed 4.5:1. The seven current-screen actions use explicit `bg_hud_control` fills, and the themed Button background tint is cleared for those buttons. Their source edges exceed 3:1 in default/focused/pressed states; visible HUD idle alpha remains 1.0. This is not a blanket R-25 PASS: switch thumb/track, sheet button states, progress tint, disabled-alpha compositing, sleeping fades, DayNight resolution, and real native state rendering remain unverified. The decorative sheet rim does not identify an action. Outdoor glare and panel brightness require device evidence.

## Delivery Gate, source-only status

`PASS-SOURCE` means only the named inspection succeeded. `UNVERIFIED` is not PASS. Open findings prevent release; no overall UI approval follows from this table.

| Gate | Status and evidence |
| --- | --- |
| R-02 copy | PASS-SOURCE for this document: no em dash authored. Existing production text was not rewritten. |
| R-03 mobile/targets | PASS-SOURCE for explicit 48dp minimum target dimensions on all seven current-screen actions and sheet recording button heights. UNVERIFIED rendered overflow/200% font scaling, short screens and actual bounds. |
| R-17/R-18/R-36/R-38 honesty | PASS-SOURCE for handoff: no invented testimonials, metrics, execution results, identity assurance, or runtime PASS. Counts/ratios link to source or executed tools. |
| R-23 assets | PASS-SOURCE: only the explicitly authorized functional native state drawable was added; no decorative assets. |
| R-24/R-28 navigation/FAQ | N/A: no web navbar or FAQ added. Native destinations are listed above. |
| R-25 text/non-text contrast | PASS-SOURCE for measured explicit current-screen boundaries (minimum 7.34:1) and focused/pressed edges (minimum 7.61:1). PARTIAL overall: decorative rim is 2.78:1, native states/compositing remain UNVERIFIED. |
| R-26/C-2 controls | PARTIAL-SOURCE: callbacks and service guards traced per control. Processing deadline now host-tested and source-wired; actual interaction/backend completion UNVERIFIED. Backend child remains pending. |
| R-27 states | PASS-SOURCE for visible branches: unknown profile, acquiring, recording, uploading/processing, saving/saved, error/releasing/closed, diagnostic ready, missing-result timeout and explicit recovery. Service execution remains UNVERIFIED. |
| R-32 keyboard/focus | PASS-SOURCE for all seven current-screen actions' explicit focusable state-list backgrounds and named status setup action. UNVERIFIED keyboard/TalkBack/D-pad/Escape/focus-return execution. |
| R-33 source patching | PASS-SOURCE: direct source edits only; no source-rewriting helper. XML probe is scratch-only and read-only. |
| R-34 themes | UNVERIFIED: no toggle added; inherited DayNight defaults still require light/dark system-mode inspection. |
| R-35 execution | SOURCE-INSPECTION EXCEPTION ONLY: explicit no-Android-execution instruction. 26-file XML parse, 635-case host/Python/source suite, and per-control source walkthrough recorded; no build, run, console, or click-through evidence. NOT release ready. |
| R-37 direction | PASS-SOURCE: existing dark/cyan identity and ENERGY 1 / RHYTHM 1 / MOTION 1 retained. |
| R-39 native semantics/states | PARTIAL-SOURCE: native controls, text feedback, live region, named copy action. UNVERIFIED focus/state rendering, announcements, reduced motion and assistive input. Web pointer/ARIA rules are N/A. |
| R-40 performance | N/A for new web media/scripts; none added. Android performance, latency, CPU and thermal behavior not measured. |
| Purpose gates R-01/04/06/07/08/09/10/12/13/14/19/22 | PASS-SOURCE for the functional focus/press drawable and no decorative assets or new techniques. Existing dark/cyan, modal grouping, mascot and status hierarchy have purposes above. Existing glow/motion dose and rendered hierarchy remain UNVERIFIED. |
| Liveliness and R-05/11/15/16/20/21/29/30/31 | PARTIAL-SOURCE: explicit dials, native appliance composition, specific recording CTAs, retained palette and modal hierarchy. Focal point, whitespace, identity and motion consistency require rendered review. No clone or new template introduced. |
| C-1/C-3/C-5 craftsmanship | PASS-SOURCE for documented purposes, content-driven controls, and bounded evidence claims. |
| C-4 resilience | UNVERIFIED overall: errors/exits and bounded missing-result policy exist in source/host evidence, but runtime layout, service scheduling/state transitions and hardware remain untested. |

## Parent verification and release boundary

The implementation run independently established **635 passed** with the exact full-suite command above. The parent must independently inspect the resulting local commit SHA/status, verify the explicit-path staged file set, and check the backend child's contract. All incoming working-tree modifications were explicitly task-owned. Only the task-generated repository `test-results.txt` scratch log was removed; no unrelated untracked artifacts were deleted. No task-board transition, push, deploy or restart was performed.

Re-run from the repository root when reviewing:

```sh
PYTHONPATH=server/src server/.venv/bin/python -m pytest server/tests -q -o asyncio_default_fixture_loop_scope=function
git diff --check
git status --short
```

This is the repository host/Python suite, not an Android build. SDK-free Java harnesses require a JDK; report any future skip or warning honestly. Backend child `t_d226debf` is precreated and pending after this implementation phase, not an instruction to claim integrated completion. No new board tasks were created.

**The no-Android-build instruction means this remains a source-only handoff, NOT release ready.** A later authorized verification must cover actual Android compilation, runtime service lifecycle, every control/exit, font scaling and short displays, focus/assistive input, theme state colors, permissions, reconnects, missing ACK/results, physical recorder stop, playback drain, diagnostic deletion, mic routes/AEC, outdoor noise/glare, and sustained hardware behavior. None of those runtime/layout/focus/hardware checks is claimed here.
