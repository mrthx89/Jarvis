# Mobile bottom sheet and reply copy

Source changes only. No Gradle invocation, APK build, emulator run, or remote build CI is authorized for this iteration. Python/XML checks do not prove Android rendering or compilation. The owner must build/install the changed client on their own machine before these changes appear on the device.

## Changes

- Credential sheet uses an expanded window-height scroll viewport, with system bar/cutout padding and `ADJUST_RESIZE` for the keyboard.
- Token label/helper are external wrapping TextViews rather than the single-line floating hint/helper area. Validation errors use the Material 1.11.0 default indicator; their wrapping remains a device acceptance check.
- Actions use full-width wrapping labels and content-based heights; cancel/reset no longer compete in weighted columns.
- The console has an explicit “Salin jawaban terakhir” action, initially disabled. It copies the full last final assistant reply, not the six visible lines, current user transcription, or connection status. It retains the previous reply while a new user utterance is displayed. Nothing is automatically copied. The reply remains only in Activity memory; recreation clears the copy state. Clipboard contents can be visible to the OS or other permitted apps, so copy sensitive replies deliberately.

## Compact status and explicit voice control

- Status notch is content-sized and constrained within screen margins, with smaller 12sp labels and a bounds transition when status text changes. Full diagnostics remain in accessibility text; the console shows diagnostics when no transcript has arrived.
- “Mulai mikrofon” / “Berhenti” controls voice capture through the service, not just the UI. Initial installation defaults to stopped. Stopped state must survive Activity recreation, credential refresh, and endpoint updates.
- Verify STOP during connection, recording, processing and playback: the Android microphone privacy indicator must turn off, speaker playback must stop, and there must be no reconnect/capture until START. Microphone start still requires permission, valid credentials and server protocol acknowledgement. A bound foreground service/notification may remain; stopping voice is not force-stopping the app.
- Check the notch grows/shrinks for different labels without exceeding narrow screen bounds and without moving controls over the console. Tap targets remain at least 48dp for the separate voice button.

## ON/OFF mascot feedback

OFF selects the existing crying expression with a gentle vertical tremble and the caption “Anda jahat… Jarvis menangis. Mikrofon tetap mati.” ON shows a happy/optimistic expression with a short bounce for two seconds, then returns to the latest conversation expression. Initial state synchronization does not replay the ON celebration. Captions are separate from the reply so copying still retains the last complete assistant answer. These are local visual reactions, not TTS or microphone commands; the compact status remains the source of connection/readiness information. Check rapid ON/OFF, incoming state events during the reaction, and app reattachment on the actual device. Animation smoothness and rendering remain unverified.

## Owner device acceptance

1. Check a 320dp-wide phone, a typical 360–412dp portrait phone, and a tablet. Try font scaling 1.0, 1.3 and 2.0.
2. Long-press the status text. Scroll from title to reset action; all labels must remain readable, including ID/server strings.
3. Open token help, then tap the token field to show the keyboard. Scroll to trust/save/reset without content hiding behind keyboard, navigation bar or cutout. Close/reopen keyboard and sheet repeatedly; padding must not accumulate.
4. Use invalid input to check wrapped error text. Verify the trust checkbox and reset confirmation remain functional. Do not capture real token screenshots.
5. Before the first reply, copy must be disabled. Receive a long final Jarvis reply and tap copy; paste into a local editor and compare the entire reply, including lines beyond the visible subtitle area.
6. Speak another utterance. Copy should still copy the previous Jarvis reply until the next final assistant reply arrives, never the user's utterance or status.
7. Verify normal subtitle scrolling, mascot space, and screen-reader button labels. None of these hardware/layout checks were executed on the server.
