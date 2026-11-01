"""Private subprocess boundary for Hermes slash command; stdout is non-secret JSON."""
import argparse
import json
import os
from pathlib import Path
import stat
import sys
from urllib.request import Request, urlopen


def read_settings(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError('Private settings required')
        return json.load(stream)


def send_protected(text, settings):
    from dotenv import dotenv_values
    token = dotenv_values(settings['telegram_env_file']).get('TELEGRAM_BOT_TOKEN')
    if not token:
        raise ValueError('Telegram is not configured')

    formatted_text = text
    reply_markup = None
    try:
        data = json.loads(text)
        if isinstance(data, dict) and 'token' in data and 'device_id' in data:
            dev_id = data['device_id']
            tok = data['token']
            orig = data.get('origin', '')
            formatted_text = (
                f"🔑 <b>Token Akses Nexo Appliance</b>\n\n"
                f"<b>Device ID:</b>\n<code>{dev_id}</code>\n\n"
                f"<b>Token:</b>\n<code>{tok}</code>\n\n"
                f"<b>Origin:</b>\n<code>{orig}</code>\n\n"
                f"<i>Ketuk token di atas atau gunakan tombol di bawah untuk menyalin ke clipboard.</i>"
            )
            reply_markup = {
                "inline_keyboard": [
                    [
                        {
                            "text": "📋 Salin Token",
                            "copy_text": {"text": tok}
                        }
                    ]
                ]
            }
    except Exception:
        pass

    msg_payload = {
        'chat_id': str(settings['owner_chat_id']),
        'text': formatted_text,
        'parse_mode': 'HTML' if reply_markup else None,
        'disable_web_page_preview': True
    }
    if reply_markup:
        msg_payload['reply_markup'] = reply_markup

    payload = json.dumps(msg_payload).encode()
    request = Request('https://api.telegram.org/bot' + token + '/sendMessage',
                      data=payload, headers={'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=15) as response:
            data = json.load(response)
        if data.get('ok') is not True or not data.get('result', {}).get('message_id'):
            raise ValueError('Delivery failed')
    except Exception:
        if reply_markup:
            try:
                msg_payload.pop('reply_markup', None)
                payload_fallback = json.dumps(msg_payload).encode()
                req_fallback = Request('https://api.telegram.org/bot' + token + '/sendMessage',
                                       data=payload_fallback, headers={'Content-Type': 'application/json'})
                with urlopen(req_fallback, timeout=15) as response:
                    data = json.load(response)
                if data.get('ok') is True and data.get('result', {}).get('message_id'):
                    return
            except Exception:
                pass
        raise ValueError('Private Telegram delivery failed') from None


def send_device_exists_card(device_id, settings):
    from dotenv import dotenv_values
    token = dotenv_values(settings['telegram_env_file']).get('TELEGRAM_BOT_TOKEN')
    if not token:
        return

    delivery_file = Path(settings['delivery_dir']) / f"{device_id}.json"
    existing_token = None
    existing_origin = None
    if delivery_file.exists():
        try:
            data = json.loads(delivery_file.read_text())
            existing_token = data.get('token')
            existing_origin = data.get('origin')
        except Exception:
            pass

    buttons = []
    if existing_token:
        buttons.append([
            {
                "text": "📋 Salin Token",
                "copy_text": {"text": existing_token}
            }
        ])
    buttons.append([
        {
            "text": "🔄 Salin Perintah Rotasi",
            "copy_text": {"text": f"/nexo_token rotate {device_id}"}
        }
    ])

    formatted_text = (
        f"📱 <b>Perangkat Sudah Terdaftar</b>\n\n"
        f"<b>Device ID:</b>\n<code>{device_id}</code>\n\n"
    )
    if existing_token:
        formatted_text += (
            f"<b>Token Aktif:</b>\n<code>{existing_token}</code>\n\n"
            f"<b>Origin:</b>\n<code>{existing_origin or settings.get('origin', '')}</code>\n\n"
            f"<i>Ketuk token di atas atau gunakan tombol di bawah untuk menyalin langsung ke clipboard:</i>"
        )
    else:
        formatted_text += (
            f"<i>Gunakan tombol di bawah untuk menyalin perintah rotasi token baru:</i>"
        )

    msg_payload = {
        'chat_id': str(settings['owner_chat_id']),
        'text': formatted_text,
        'parse_mode': 'HTML',
        'reply_markup': {"inline_keyboard": buttons},
        'disable_web_page_preview': True
    }
    payload = json.dumps(msg_payload).encode()
    request = Request('https://api.telegram.org/bot' + token + '/sendMessage',
                      data=payload, headers={'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=15):
            pass
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings', required=True)
    args = parser.parse_args()
    try:
        from .telegram_provision import handle_command
        settings = read_settings(args.settings)
        request = json.loads(sys.stdin.read(8193))
        summary = handle_command(request['args'], request['source'], settings,
                                 lambda text: send_protected(text, settings))
        if isinstance(summary, str) and 'Device exists; use deliver ' in summary:
            parts = summary.split('Device exists; use deliver ', 1)[1].split(' ', 1)
            if parts:
                dev_id = parts[0].strip()
                send_device_exists_card(dev_id, settings)
    except Exception:
        summary = 'Pengelolaan token gagal. Periksa konfigurasi privat; tidak ada token di log.'
    print(json.dumps({'summary': summary}))


if __name__ == '__main__':
    main()
