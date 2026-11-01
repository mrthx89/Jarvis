"""Deterministic slash command. No LLM tool and no raw credential in reply text."""
import json
import os
from pathlib import Path
import subprocess


def _handle(raw_args, *, source=None, platform=None):
    # Trust adapter-supplied source only, never identity embedded in raw_args.
    if source is None or platform != 'telegram':
        return 'Perintah ini hanya tersedia melalui chat privat Telegram pemilik.'
    metadata = {key: getattr(source, key, None) for key in
                ('chat_id', 'user_id', 'chat_type', 'is_bot')}
    metadata['platform'] = platform
    try:
        settings_path = Path(__file__).with_name('settings.json')
        settings = json.loads(settings_path.read_text())
        # Reject before launching the worker, as well as inside the worker.
        if (metadata['chat_type'] != 'dm' or metadata['is_bot'] is not False
                or str(metadata['user_id']) != str(settings['owner_user_id'])
                or str(metadata['chat_id']) != str(settings['owner_chat_id'])):
            return 'Ditolak. Hanya pemilik di chat privat yang dapat mengelola token.'
        env = os.environ.copy()
        env['PYTHONPATH'] = settings['server_src']
        result = subprocess.run(
            [settings['backend_python'], '-m', 'jarvis_server.telegram_command',
             '--settings', str(settings_path)],
            input=json.dumps({'args': raw_args, 'source': metadata}),
            text=True, capture_output=True, timeout=40, env=env,
        )
        if result.returncode != 0:
            return 'Pengelolaan token gagal. Hubungi pengelola; token tidak ditampilkan di log.'
        # Worker returns a non-secret summary only; stderr is never forwarded.
        return json.loads(result.stdout)['summary']
    except Exception:
        return 'Pengelolaan token belum tersedia. Periksa konfigurasi server privat.'


def register(ctx):
    ctx.register_command('nexo-token', handler=_handle,
                         description='Kelola token perangkat Nexo (pemilik, chat privat).',
                         args_hint='DEVICE_ID | deliver DEVICE_ID | rotate DEVICE_ID | revoke DEVICE_ID')
    ctx.register_command('nexo_token', handler=_handle,
                         description='Kelola token perangkat Nexo (pemilik, chat privat).',
                         args_hint='DEVICE_ID | deliver DEVICE_ID | rotate DEVICE_ID | revoke DEVICE_ID')
    ctx.register_command('jarvis-token', handler=_handle,
                         description='Kelola token perangkat Jarvis (pemilik, chat privat).',
                         args_hint='DEVICE_ID | deliver DEVICE_ID | rotate DEVICE_ID | revoke DEVICE_ID')
    ctx.register_command('jarvis_token', handler=_handle,
                         description='Kelola token perangkat Jarvis (pemilik, chat privat).',
                         args_hint='DEVICE_ID | deliver DEVICE_ID | rotate DEVICE_ID | revoke DEVICE_ID')
