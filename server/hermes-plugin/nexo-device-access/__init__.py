"""Deterministic slash commands for Nexo Appliance Telegram Bot."""
import json
import os
from pathlib import Path
import subprocess
from urllib.request import Request, urlopen


def _handle_token(raw_args, *, source=None, platform=None):
    """Manage device access tokens."""
    if source is None or platform != 'telegram':
        return 'Perintah ini hanya tersedia melalui chat privat Telegram pemilik.'
    metadata = {key: getattr(source, key, None) for key in
                ('chat_id', 'user_id', 'chat_type', 'is_bot')}
    metadata['platform'] = platform
    try:
        settings_path = Path(__file__).with_name('settings.json')
        settings = json.loads(settings_path.read_text())
        if (metadata['chat_type'] != 'dm' or metadata['is_bot'] is not False
                or str(metadata['user_id']) != str(settings['owner_user_id'])
                or str(metadata['chat_id']) != str(settings['owner_chat_id'])):
            return 'Ditolak. Hanya pemilik di chat privat yang dapat mengelola token.'
        env = os.environ.copy()
        env['PYTHONPATH'] = settings['server_src']
        module_name = 'nexo_server.telegram_command'
        result = subprocess.run(
            [settings['backend_python'], '-m', module_name,
             '--settings', str(settings_path)],
            input=json.dumps({'args': raw_args, 'source': metadata}),
            text=True, capture_output=True, timeout=40, env=env,
        )
        if result.returncode != 0:
            return 'Pengelolaan token gagal. Hubungi pengelola; token tidak ditampilkan di log.'
        return json.loads(result.stdout)['summary']
    except Exception:
        return 'Pengelolaan token belum tersedia. Periksa konfigurasi server privat.'


# Alias for backward compatibility with existing tests
_handle = _handle_token


def _handle_status(raw_args, *, source=None, platform=None):
    """Check live status of the Nexo appliance & gateway server."""
    if source is None or platform != 'telegram':
        return 'Perintah ini hanya tersedia melalui chat privat Telegram pemilik.'
    try:
        with urlopen('http://127.0.0.1:8765/session/status', timeout=5) as resp:
            data = json.load(resp)
        online = data.get('server_online', True)
        appliance = data.get('appliance_connected', False)
        state = data.get('state', 'UNKNOWN')
        thread = data.get('session_thread', 'nexo')
        dev_id = data.get('device_id') or '—'
        subs = data.get('active_subscribers', 0)
        return (
            "📊 <b>STATUS NEXO APPLIANCE</b>\n\n"
            f"• <b>Gateway Server:</b> {'ONLINE 🟢' if online else 'OFFLINE 🔴'}\n"
            f"• <b>Layar / Tablet:</b> {'TERHUBUNG 🟢' if appliance else 'TIDAK TERHUBUNG ⚪'}\n"
            f"• <b>Device ID:</b> <code>{dev_id}</code>\n"
            f"• <b>State Wajah:</b> <code>{state}</code>\n"
            f"• <b>Hermes Thread:</b> <code>{thread}</code>\n"
            f"• <b>Active Stream:</b> {subs} subscriber"
        )
    except Exception as e:
        return f"Gagal membaca status: {e}. Pastikan layanan nexo-appliance aktif."


def _handle_say(raw_args, *, source=None, platform=None):
    """Command tablet to speak text directly via audio output."""
    if source is None or platform != 'telegram':
        return 'Perintah ini hanya tersedia melalui chat privat Telegram pemilik.'
    text = (raw_args or '').strip()
    if not text:
        return "Penggunaan: <code>/nexo_say &lt;pesan teks&gt;</code>\nContoh: <code>/nexo_say Halo, saya Nexo.</code>"
    try:
        req_data = json.dumps({'text': text}).encode()
        req = Request('http://127.0.0.1:8765/session/speak', data=req_data, headers={'Content-Type': 'application/json'})
        with urlopen(req, timeout=10) as resp:
            data = json.load(resp)
        if data.get('status') == 'ok' and data.get('spoken'):
            return f"🔊 Nexo telah menyuarakan di layar:\n\"{text}\""
        else:
            msg = data.get('message', 'Tablet/appliance tidak terhubung.')
            return f"⚠️ {msg}"
    except Exception as e:
        return f"Gagal mengirim audio: {e}"


def _handle_help(raw_args, *, source=None, platform=None):
    """Show help guide for all Nexo Telegram commands."""
    return (
        "🤖 <b>PERINTAH TELEGRAM NEXO APPLIANCE</b>\n\n"
        "• <code>/nexo_token &lt;DEVICE_ID&gt;</code>\n"
        "  Terbitkan token akses atau kirim ulang tombol salin token.\n\n"
        "• <code>/nexo_status</code>\n"
        "  Cek status online server, status koneksi tablet, dan ekspresi wajah.\n\n"
        "• <code>/nexo_say &lt;pesan teks&gt;</code>\n"
        "  Perintahkan speaker/layar tablet Nexo berucap langsung via suara.\n\n"
        "• <code>/nexo_help</code>\n"
        "  Menampilkan panduan perintah ini."
    )


def register(ctx):
    # Token management
    ctx.register_command('nexo-token', handler=_handle_token,
                         description='Kelola token perangkat Nexo (pemilik, chat privat).',
                         args_hint='DEVICE_ID | deliver DEVICE_ID | rotate DEVICE_ID | revoke DEVICE_ID')
    ctx.register_command('nexo_token', handler=_handle_token,
                         description='Kelola token perangkat Nexo (pemilik, chat privat).',
                         args_hint='DEVICE_ID | deliver DEVICE_ID | rotate DEVICE_ID | revoke DEVICE_ID')

    # Status check
    ctx.register_command('nexo-status', handler=_handle_status,
                         description='Cek status koneksi Nexo appliance & gateway.',
                         args_hint='')
    ctx.register_command('nexo_status', handler=_handle_status,
                         description='Cek status koneksi Nexo appliance & gateway.',
                         args_hint='')

    # Direct speech trigger
    ctx.register_command('nexo-say', handler=_handle_say,
                         description='Perintahkan tablet Nexo berbicara via audio speaker.',
                         args_hint='<pesan teks>')
    ctx.register_command('nexo_say', handler=_handle_say,
                         description='Perintahkan tablet Nexo berbicara via audio speaker.',
                         args_hint='<pesan teks>')

    # Help
    ctx.register_command('nexo-help', handler=_handle_help,
                         description='Daftar perintah khusus Nexo Appliance di Telegram.',
                         args_hint='')
    ctx.register_command('nexo_help', handler=_handle_help,
                         description='Daftar perintah khusus Nexo Appliance di Telegram.',
                         args_hint='')
