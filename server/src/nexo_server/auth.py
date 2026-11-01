"""Owner-operated per-device credentials for Nexo Appliance. Never expose this CLI over HTTP."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
from urllib.parse import urlsplit

DEVICE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
TOKEN = re.compile(r"[A-Za-z0-9_-]{43,128}\Z")
MAX_REGISTRY_BYTES = 1024 * 1024

NEXO_DEFAULT_DIR = Path("/root/.config/nexo-auth")
NEXO_DEFAULT_REGISTRY = NEXO_DEFAULT_DIR / "devices.json"
JARVIS_DEFAULT_REGISTRY = Path("/root/.config/nexo-auth/devices.json")


def resolve_registry_path(path: str | Path | None = None) -> Path | None:
    """Resolve device registry path supporting /root/.config/nexo-auth/ with fallback to nexo-auth.

    If path is a directory (or ends with slash), devices.json within that directory is targeted.
    If the targeted file is under nexo-auth and does not exist, falls back to nexo-auth/devices.json.
    """
    if not path:
        return None
    p = Path(path)
    if str(path).endswith("/") or p.is_dir() or (p.suffix == "" and p.name.endswith("-auth")):
        target = p / "devices.json"
    else:
        target = p

    if target.exists():
        return target

    # Fallback from nexo-auth to nexo-auth
    if "nexo-auth" in str(target) or target.name == "devices.json":
        if target.parent.name == "nexo-auth":
            peer = target.parent.parent / "nexo-auth" / target.name
            if peer.exists():
                return peer
        if "nexo-auth" in str(target) and JARVIS_DEFAULT_REGISTRY.exists():
            return JARVIS_DEFAULT_REGISTRY

    return target


def credential_origin(url):
    parts = urlsplit(url)
    if (parts.scheme != 'wss' or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.query or parts.fragment
            or '?' in url or '#' in url):
        raise ValueError('A clean WSS origin is required')
    port = parts.port if parts.port is not None else 443
    if not 1 <= port <= 65535:
        raise ValueError('Invalid port')
    return f"wss://{parts.hostname.lower()}:{port}"


class DeviceRegistry:
    def __init__(self, path=None):
        self.raw_path = Path(path) if path else None
        self.path = resolve_registry_path(path)

    def read(self):
        if self.raw_path is not None:
            self.path = resolve_registry_path(self.raw_path)
        if self.path is None:
            raise ValueError("Registry is not configured")
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'r', encoding='utf-8') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > MAX_REGISTRY_BYTES:
                raise ValueError("Registry must be a bounded private regular file")
            data = json.loads(stream.read(MAX_REGISTRY_BYTES + 1))
        if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('devices'), dict):
            raise ValueError("Invalid registry")
        for device, entry in data['devices'].items():
            if not DEVICE_ID.fullmatch(device) or not isinstance(entry, dict):
                raise ValueError("Invalid device")
            if set(entry) != {'token_sha256'} or not re.fullmatch('[0-9a-f]{64}', str(entry['token_sha256'])):
                raise ValueError("Registry may contain only token hashes")
        return data

    def authenticate(self, device_id, authorization):
        if not DEVICE_ID.fullmatch(device_id or ''):
            return False
        if not authorization or not authorization.startswith('Bearer '):
            return False
        token = authorization[7:]
        if not TOKEN.fullmatch(token):
            return False
        try:
            entry = self.read()['devices'].get(device_id)
            digest = hashlib.sha256(token.encode('ascii')).hexdigest()
            return hmac.compare_digest(digest, entry['token_sha256'] if entry else '0' * 64) and entry is not None
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
            return False


def _write_private(path, data, exclusive=False):
    encoded = (json.dumps(data, indent=2) + '\n').encode()
    if exclusive:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        return
    fd, temporary = tempfile.mkstemp(prefix='.nexo-registry-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def _registry_lock(path):
    fd = os.open(str(path) + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        yield


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['provision', 'revoke'])
    parser.add_argument('--registry', required=True, type=Path)
    parser.add_argument('--device-id', required=True)
    parser.add_argument('--output', type=Path, help='New private credential file; never stdout')
    parser.add_argument('--origin', help='Trusted WSS endpoint; recorded in private delivery file')
    args = parser.parse_args()
    if not DEVICE_ID.fullmatch(args.device_id):
        parser.error('Invalid device ID')
    if args.action == 'provision' and (args.output is None or args.origin is None):
        parser.error('Provision requires --output and --origin')
    try:
        registry_target = args.registry
        if str(registry_target).endswith("/") or registry_target.is_dir() or (registry_target.suffix == "" and registry_target.name.endswith("-auth")):
            registry_target = registry_target / "devices.json"

        with _registry_lock(registry_target):
            try:
                data = DeviceRegistry(registry_target).read()
            except FileNotFoundError:
                if registry_target.is_symlink():
                    raise ValueError('Invalid registry')
                fallback = resolve_registry_path(registry_target)
                if fallback and fallback.exists() and fallback != registry_target:
                    data = DeviceRegistry(fallback).read()
                else:
                    data = {'version': 1, 'devices': {}}
            if args.action == 'provision':
                if args.device_id in data['devices']:
                    raise ValueError('Device exists; revoke before replacing')
                bound_origin = credential_origin(args.origin)
                token = secrets.token_urlsafe(32)
                _write_private(args.output, {'device_id': args.device_id, 'token': token,
                                             'origin': bound_origin}, exclusive=True)
                data['devices'][args.device_id] = {'token_sha256': hashlib.sha256(token.encode()).hexdigest()}
            else:
                data['devices'].pop(args.device_id, None)
            _write_private(registry_target, data)
    except (OSError, ValueError):
        # Avoid printing paths/payloads/tokens from exceptions.
        parser.exit(1, 'Credential operation failed; check private paths, permissions and device state.\n')
    print('Credential operation complete. No token is printed.')


if __name__ == '__main__':
    main()
