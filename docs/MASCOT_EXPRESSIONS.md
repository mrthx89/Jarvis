# Contextual mascot expressions and direct commands

Native assets remain local and the voice privacy controls are unchanged. This iteration does not authorize an Android build on the server. Python tests and Android source guards are not device rendering or Kotlin compilation checks.

## Intended interaction

While voice is ON and ready, say a short standalone instruction such as “Nexo, tersenyum” (atau “Jarvis, tersenyum”), “tunjukkan wajah bingung”, “coba tertawa”, or “kembali normal”. Supported standalone commands receive a fixed acknowledgement and bypass the reasoning agent; unsupported wording, questions about expressions, and mixed tasks still follow the normal agent path. No command turns the microphone on. OFF stops the audio/socket path, so spoken commands cannot be heard while OFF.

Supported examples also include “ekspresi marah”, “ekspresi mengantuk”, “ekspresi cinta”, “berkedip”, “ekspresi jahil”, and “jangan sedih” (smile). The available labels match the 16 bundled Android expressions: netral, senang, sangat senang, tertawa, bingung, mengantuk, mengedip, terkejut, cinta, jahil, keren, malu, marah, menangis, sedih, takut. These are short reactions, not a permanent mood lock. Quoted examples and mixed tasks such as “Nexo tersenyum lalu buka email” are not local face commands and still go to the agent. The local acknowledgement is “Baik, ekspresi wajah sudah diubah.”

Normal replies can select a restrained expression based on bounded Indonesian phrases in the recognized utterance and reply. This is deterministic contextual matching, not human emotion detection or unrestricted understanding of every instruction. It does not change the reasoning model or persist a personality instruction.

## Native expression timing

- Existing ON celebration (two seconds) and OFF crying face retain priority.
- A non-neutral speaking expression remains for six seconds after SPEAKING transitions to IDLE or ready LISTENING, then returns to the ordinary idle expression. The service preserves the selected expression during TTS start and queued playback; the hold begins when its state leaves SPEAKING after playback drain. Exact audible timing remains a device check.
- A new thinking/speaking/error/disconnected state interrupts the hold; ready LISTENING may retain it briefly while microphone readiness stays truthful in the status. OFF clears it. Repeated IDLE renders do not extend it.
- Listening uses a neutral/focused face by default rather than a startled face. Existing gaze, tilt, blink and breathing remain local animations.
- Expressions are cross-fades between existing artwork, not newly generated geometry or full lip synchronization.

## Owner device checks

1. Test standalone smile/laugh/confused/neutral commands with microphone ON; verify matching face and acknowledgement.
2. Ask an ordinary task containing an expression word and verify it is not swallowed as a face command.
3. Share a short sad statement, greeting, or successful outcome; compare the response face with the intended conservative context mapping.
4. Wait after a response; confirm return toward neutral, then start another utterance during the hold and confirm the next THINKING state takes priority.
5. Stop voice during a reaction. Confirm crying face, hidden console, stopped playback and no microphone privacy indicator; spoken commands must not restart voice.
6. Retest clipboard and 15-second ON caption behavior. These acceptance checks require the owner's newly built Android client and have not been executed on the server.
