# Stage 1 security boundaries

Device authorization is not speaker recognition or an agent sandbox. A permitted tablet can still capture nearby speech, a TV, or malicious spoken instructions. Wake-word, VAD and device tokens do not prove the owner's intent.

Before giving the appliance write-capable tools:

- Run the gateway/agent as a dedicated unprivileged OS account, with only required paths and credentials. The existing host service is not claimed migrated by this repository change.
- Keep destructive commands, external publishing, purchases, and permission changes behind explicit confirmation. Enforce tool permissions in the agent runtime; a conversational instruction alone is not a security boundary.
- Restrict the old/public gateway at the network or tunnel until authenticated migration is complete. Source-code changes do not patch an already running process.
- Store device tokens only in the external private provisioning file and Android Keystore-encrypted local storage; never Remote Config, URLs, source, or routine logs.
- Revoke a lost device and remove its local credential; a token is a bearer capability, not a user password. Protect the phone and the owner's provisioning machine.
- Keep endpoint changes origin-bound so configuration cannot forward a saved bearer credential to an unapproved WSS host.
- Review public `/health` as liveness only; it is not proof STT, TTS, or agent inference is ready.

Do not claim production hardening complete until signing-key exposure has been evaluated, the authenticated backend has been activated, and the updated client has been tested on real hardware. The manual CI workflow and Android tests have not been executed for this iteration, as requested by the owner.
