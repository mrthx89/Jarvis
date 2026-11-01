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
    # dotenv is already supplied by uvicorn[standard]; do not print any value.
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
                f"🔑 <b>Token Akses Jarvis Appliance</b>\n\n"
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

    # Note: protect_content is omitted so the user can select and copy the token.
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
        # URL-bearing HTTP errors include the bot credential. Never propagate them.
        raise ValueError('Private Telegram delivery failed') from None


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
    except Exception:
        summary = 'Pengelolaan token gagal. Periksa konfigurasi privat; tidak ada token di log.'
    print(json.dumps({'summary': summary}))


if __name__ == '__main__':
    main()
