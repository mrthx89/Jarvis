"""Owner-only Telegram provisioning for Nexo Appliance. Secret delivery never uses the return value."""
from collections.abc import Callable
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import stat

from .auth import DEVICE_ID, TOKEN, DeviceRegistry, _registry_lock, _write_private, credential_origin, resolve_registry_path

_USAGE = 'Usage: DEVICE_ID | rotate DEVICE_ID | deliver DEVICE_ID | revoke DEVICE_ID | help'


def handle_command(raw_args, source: dict, settings: dict, send_secret: Callable[[str], object]) -> str:
    """Issue a credential and deliver it through the caller's protected sender."""
    if (not isinstance(source, dict) or not isinstance(settings, dict)
            or source.get('platform') != 'telegram' or source.get('chat_type') != 'dm'
            or source.get('is_bot') is not False
            or not isinstance(settings.get('owner_user_id'), str) or not settings['owner_user_id']
            or not isinstance(settings.get('owner_chat_id'), str) or not settings['owner_chat_id']
            or source.get('user_id') != settings['owner_user_id']
            or source.get('chat_id') != settings['owner_chat_id']):
        return 'Access denied: owner Telegram DM required.'
    args = raw_args.split() if isinstance(raw_args, str) else []
    if args == ['help']:
        return _USAGE
    if len(args) == 1 and args[0] not in {'rotate', 'deliver', 'revoke'}:
        action, device_id = 'create', args[0]
    elif len(args) == 2 and args[0] in {'rotate', 'deliver', 'revoke'}:
        action, device_id = args
    else:
        return _USAGE
    if not DEVICE_ID.fullmatch(device_id):
        return _USAGE
    try:
        path = Path(settings['registry_path'])
        if str(path).endswith("/") or path.is_dir() or (path.suffix == "" and path.name.endswith("-auth")):
            path = path / "devices.json"
        delivery = Path(settings['delivery_dir']) / (device_id + '.json')
        maximum = settings.get('max_devices', 100)
        if type(maximum) is not int or maximum < 1:
            raise ValueError('Invalid device limit')
        for parent in {path.parent, delivery.parent}:
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError('Private parent required')
        if path.absolute() == delivery.absolute():
            raise ValueError('Separate registry and delivery required')
        with _registry_lock(path):
            try:
                data = DeviceRegistry(path).read()
            except FileNotFoundError:
                if path.is_symlink():
                    raise ValueError('Invalid registry')
                fallback = resolve_registry_path(path)
                if fallback and fallback.exists() and fallback != path:
                    data = DeviceRegistry(fallback).read()
                else:
                    data = {'version': 1, 'devices': {}}
            devices = data['devices']
            if action == 'create' and device_id in devices:
                return f'Device exists; use deliver {device_id} to resend or rotate {device_id} to replace.'
            if action == 'create' and len(devices) >= maximum:
                return 'Device limit reached; revoke an existing device first.'
            if action in {'rotate', 'deliver'} and device_id not in devices:
                return 'Device not found; create it with DEVICE_ID first.'
            if action == 'revoke':
                devices.pop(device_id, None)
                _write_private(path, data)
                delivery.unlink(missing_ok=True)
                return 'Device revoked; credentials are no longer valid.'
            if action == 'deliver':
                payload = _read_delivery(delivery, device_id, devices[device_id]['token_sha256'])
            else:
                token = secrets.token_urlsafe(32)
                payload = {'device_id': device_id, 'token': token,
                           'origin': credential_origin(settings['origin'])}
                _write_private(delivery, payload)
                devices[device_id] = {'token_sha256': hashlib.sha256(token.encode('ascii')).hexdigest()}
                _write_private(path, data)
            try:
                if send_secret(json.dumps(payload)) is False:
                    raise ValueError('Delivery rejected')
            except Exception:
                return f'Credential preserved; private delivery failed. Retry: deliver {device_id}'
        return 'Credential ready; credentials sent privately.'
    except Exception:
        return 'Credential operation failed; check private configuration and device state.'


def _read_delivery(path, device_id, digest):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'r', encoding='utf-8') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 4096:
            raise ValueError('Invalid delivery file')
        payload = json.loads(stream.read(4097))
    if (not isinstance(payload, dict) or set(payload) != {'device_id', 'token', 'origin'}
            or payload['device_id'] != device_id or not isinstance(payload['token'], str)
            or not TOKEN.fullmatch(payload['token']) or not isinstance(payload['origin'], str)
            or credential_origin(payload['origin']) != payload['origin']
            or not hmac.compare_digest(hashlib.sha256(payload['token'].encode('ascii')).hexdigest(), digest)):
        raise ValueError('Invalid delivery')
    return payload
