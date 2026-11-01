# Owner-only Telegram device provisioning

The appliance WebSocket requires an existing registered device ID plus its matching bearer token before accept. Device ID alone, arbitrary token, or a token belonging to another ID cannot authorize audio. `/health` remains public liveness only and does not permit agent use. Missing/invalid registry fails closed. The live authenticated gateway was activated separately from repository publication.

## Use in the owner's private bot chat

    /jarvis_token DEVICE_ID

Replace DEVICE_ID with the exact ID shown by long-pressing the Android status. Telegram normalizes underscores to the registered `jarvis-token` command. This is a deterministic native Hermes slash command, not an LLM interpreting arbitrary provisioning instructions. Do not paste raw bearer tokens into normal conversations.

Additional explicit operations:

    /jarvis_token deliver DEVICE_ID
    /jarvis_token rotate DEVICE_ID
    /jarvis_token revoke DEVICE_ID

Creating an already registered ID does not replace its token. Use `deliver` to resend the private current delivery copy, or `rotate` deliberately; rotation requires entering the new token on Android. Revocation removes server access, not Android local storage. Registry checks on existing sockets occur at the next application frame; for urgent active-session termination restart the appliance service. There is no automatic expiry.

## Authorization and secret handling

- Both Telegram numeric sender user ID and private chat ID must match the configured owner. Groups, bots, other platforms and missing context are denied before provisioning. Do not use display names as identity.
- The global Telegram gateway allowlist is not the sole boundary: the command repeats its own owner checks inside the worker.
- Registry stores hashes only; raw delivery files remain outside the repository with mode 0600 in a private directory. They are retained for explicit resend, so secure backups and remove copies when no longer needed.
- Tokens are sent directly using Telegram Bot API without `protect_content` so that the owner can select and copy the token into the appliance settings, along with an inline "📋 Salin Token" clipboard button and monospace `<code>` block. Raw tokens do not pass through the LLM or command stdout/summary. Telegram bot chats are not end-to-end encrypted. Secure the Telegram account with two-step verification. Do not call this a secret vault.
- API/transport exceptions are sanitized so bot credential URLs and message bodies are not forwarded to gateway logs.
- No public token-issuance HTTP endpoint is added. A device ID is not proof of ownership; only the configured owner can approve it.

## Installation on this host / portability

Plugin source is under `server/hermes-plugin/jarvis-device-access/`. Copy its two files to `$HERMES_HOME/plugins/jarvis-device-access/`. Create a mode-0600 `settings.json` beside the installed plugin with owner_user_id, owner_chat_id, registry_path, delivery_dir, origin, backend_python, server_src, telegram_env_file. Numeric owner IDs and absolute paths are deployment settings, never token literals. `telegram_env_file` points to the active Hermes secret environment file; do not duplicate the bot token in settings. Create delivery_dir privately, mode 0700.

Enable using `hermes plugins enable jarvis-device-access --no-allow-tool-override`. Plugin doctor checks registration; verify the live command separately. The command invokes the existing backend virtualenv through a fixed subprocess argv, sends only trusted source metadata/command args over stdin, and returns sanitized JSON summaries. Production settings and secrets are not tracked. This workflow requires the gateway version supporting plugin command source context; fail closed if missing.

No Android build or CI is needed for this server-side workflow. Test provisioning with a fake sender before any real delivery; verify the actual public gateway separately with no token, wrong/unregistered token, and a registered device handshake. Do not infer working microphone/speaker behavior from auth acceptance.
