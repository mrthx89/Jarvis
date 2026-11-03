# Voice priority repair

## Scope and evidence boundary

This repair addresses sample audio entering normal conversation and discontinuous TTS delivery. Android builds, Gradle, emulator, remote CI and APK generation are explicitly prohibited for this iteration. Python behavioral tests and source-contract checks are allowed. Passing them is not proof of Android compilation, audible smoothness or physical microphone quality.

## Enrollment interaction direction

Existing Nexo identity: dark appliance screen, cyan action accent and native Android controls. ENERGY 1 / RHYTHM 1 / MOTION 1. This is an operational recording dialog, not a decorative dashboard.

- Retain the existing bounded bottom sheet so the task stays separate from the mascot conversation.
- Use readable native sans-serif and full-width wrapping buttons to support smaller devices and larger font sizes.
- Show one primary action, Start recording. Change process text only when a real request/acknowledgement occurs.
- Use a finite progress bar for the four-second capture, not a decorative pulse that falsely suggests recording.
- Show a separate input meter because capture activity and speaker identity are different facts.
- Do not label missing or stale server status as zero enrolled profiles.
- Do not display success on button press. Deletion and registration require server acknowledgement.
- Do not create new logos, illustrations or other visual assets.

## Required sequence

1. Enable microphone, wait until ordinary conversation is idle and playback is drained.
2. Open voice management. Query authoritative profile and model availability.
3. Tap Start recording. Do not speak while waiting for server acknowledgement.
4. When Recording appears, read the displayed sample. The client captures four seconds in a dedicated enrollment route, not STT or Hermes.
5. Finish sends a distinct control. Show Saving until server confirms success/failure. No spoken conversational response should occur for the sample.
6. Cancel, Back, dismiss, permission loss, STOP, disconnect and timeout discard incomplete capture. Re-enrollment must preserve the previous profile if saving fails.
7. Deletion requires an explicit confirmation and confirmed result; failed deletion must not display an empty registry.

## Runtime preflight

A real Edge-TTS request returned 314,496 bytes of PCM16/24 kHz mono (6.552 seconds audio) in 2.131 seconds, with first PCM after 0.871 seconds. This was generated in a diagnostic process, not played to the appliance. These measurements demonstrate provider/decode availability for that request, not audible continuity or a performance guarantee.

The default speaker model file was absent during preflight. Do not treat a missing model as successful biometric enrollment. Model provisioning requires matching the upstream preprocessing contract, not merely finding an ONNX file with an 80-bin input.

The playback repair also routes bounded PCM writes off the Android UI thread, fences delayed drain callbacks by utterance, and stops active earcons before TTS. Source checks do not prove device scheduling or audio quality.

## Manual local acceptance (not yet verified)

- Build latest source on the owner's Android Studio machine; record commit and installed app version.
- Registration: waiting, recording meter/progress, saving, success. Verify no ordinary assistant reply to the sample.
- Repeat with silence, a brief/quiet sample, missing model, Wi-Fi interruption, cancellation and closing the sheet.
- Re-enroll an existing profile, fail/cancel, and verify the old profile remains.
- Delete: cancel confirmation changes nothing; confirm waits for actual result; unavailable server does not claim success.
- TTS: listen to a multi-sentence Indonesian answer, including a long answer; no missing syllables, no unintended sentence-sized silence, no overlapping chime.
- After playback, verify the next microphone turn and explicit STOP still work.
- After saving/cancelling enrollment, tap **Selesai, lanjutkan mikrofon** to explicitly reconnect capture. Back/swipe/backgrounding leaves capture paused; use STOP then Mulai on the main screen to resume.
- Check sheet with large font, narrow phone, tablet, display cutout, keyboard/D-pad and TalkBack. All controls reachable, boundaries visible, no clipped text.

## Anti-slop delivery gate

- Functionality and state checks: recorded in automated test output; source inspection is not physical click-through.
- No invented success, biometric guarantees or performance percentages may be reported.
- Purpose gate: layout, colors, typography, progress and spacing reasons are above; no new decorative assets.
- Native rendering, touch/keyboard accessibility and audible playback: pending owner device verification. This gate is not a claim that the Android UI is fully verified.
