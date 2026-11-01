# Appliance session and model

The current bridge (`server/src/jarvis_server/hermes/agent_bridge.py`) runs:

    hermes chat -c appliance --create-if-missing -q <transcription> -Q

This selects the named local Hermes session `appliance`. It does not automatically share the Telegram conversation. The deployment currently uses the default Hermes home/profile; sharing a home/config with Telegram does not share its transcript. All connected appliance devices currently use the same named thread, not per-device threads. The WebSocket `sess_...` identifier is a transport connection ID, not a new Hermes conversation.

There is no Android model picker in this change. The bridge passes neither `--model` nor `--provider`. Its resumed session/config decides the model. During the read-only investigation the stored appliance model was `Hermes`, with default provider `custom`.

Do not speak `/model ...` as a configuration instruction: recognized speech is passed through `-q`, not the interactive slash-command prompt. An agent saying it has switched models is not evidence of a runtime change.

For interactive inspection on the server, first stop voice on the tablet so it does not write the same session concurrently:

    hermes --cli chat -c appliance

At the Hermes prompt (not the shell), use `/status` and `/model MODEL_OR_ALIAS`, then `/quit`. `/model` is session-scoped; do not add `--global` for an appliance-only change. Confirm the reported model again when the appliance session resumes. Persistence/precedence across this installed CLI's later `-q` invocations was not fully verified; do not promise an appliance model change solely from the interactive command.

In Telegram, `/model MODEL_OR_ALIAS` and `/status` apply to that Telegram session. `hermes model` changes profile defaults, which is broader than appliance-only and may not override an old session's saved model. No model or profile configuration was changed by this UI task.

For a deterministic per-call override, Hermes supports `hermes chat -c appliance --model MODEL_ID --provider PROVIDER -q MESSAGE -Q`. This makes a real provider call; it is not a no-op configuration command. The bridge would need explicit model/provider configuration to enforce that on every voice turn; that feature is not included here.

## Terminal & Local Session Bridging

For dynamic two-way bridging between local terminal sessions and Nexo Appliance (sending messages, listening to cognitive streams, linking session threads, and triggering audio), see the comprehensive guide:
- **[docs/TERMINAL_SESSION_BRIDGE.md](TERMINAL_SESSION_BRIDGE.md)**

Quick commands:
- `nexo session send <pesan> [-s]` : Send text instructions and optionally speak on appliance.
- `nexo session watch` : Stream cognitive events and transcripts real-time.
- `nexo session link [--latest]` : Link appliance thread to active Hermes terminal session.
- `nexo session bridge` : Interactive two-way bridging terminal.

References:
- [Terminal Session Bridge Guide](TERMINAL_SESSION_BRIDGE.md)
- https://hermes-agent.nousresearch.com/docs/reference/cli-commands
- https://hermes-agent.nousresearch.com/docs/user-guide/sessions
