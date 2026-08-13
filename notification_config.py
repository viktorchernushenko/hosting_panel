"""Encrypted, atomic server-side configuration for notification providers."""
from __future__ import annotations

import base64
import copy
import fcntl
import hashlib
import hmac
import json
import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


PROVIDER_FIELDS = {
    'telegram': {'enabled', 'bot_token', 'chat_id', 'last_test_ok', 'last_test_at'},
    'smtp': {
        'enabled', 'host', 'port', 'username', 'password', 'sender', 'recipients',
        'encryption', 'last_test_ok', 'last_test_at',
    },
}


class NotificationConfigError(RuntimeError):
    pass


class NotificationConfigStore:
    def __init__(self, path: str, master_secret: str):
        if not master_secret or len(master_secret) < 32:
            raise NotificationConfigError('master secret is unavailable')
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(self.path.suffix + '.lock')
        derived = hmac.new(master_secret.encode(), b'myh-notification-config-v1', hashlib.sha256).digest()
        self.cipher = Fernet(base64.urlsafe_b64encode(derived))

    @staticmethod
    def empty():
        return {'version': 1, 'providers': {'telegram': {}, 'smtp': {}}}

    def _decode(self, raw: bytes):
        try:
            payload = json.loads(self.cipher.decrypt(raw).decode('utf-8'))
        except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NotificationConfigError('notification configuration cannot be decrypted') from exc
        if payload.get('version') != 1 or not isinstance(payload.get('providers'), dict):
            raise NotificationConfigError('notification configuration has an unsupported format')
        return payload

    def load(self):
        try:
            return self._decode(self.path.read_bytes())
        except FileNotFoundError:
            return self.empty()

    def save(self, payload):
        providers = payload.get('providers', {})
        for provider, values in providers.items():
            if provider not in PROVIDER_FIELDS or not isinstance(values, dict):
                raise NotificationConfigError('invalid provider configuration')
            if set(values) - PROVIDER_FIELDS[provider]:
                raise NotificationConfigError('unexpected provider configuration fields')
        encrypted = self.cipher.encrypt(json.dumps(payload, separators=(',', ':'), sort_keys=True).encode())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        lock_fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            with os.fdopen(lock_fd, 'r+b', closefd=True) as lock_handle:
                fcntl.flock(lock_handle, fcntl.LOCK_EX)
                if self.path.exists():
                    backup = self.path.with_suffix(self.path.suffix + '.bak')
                    backup.write_bytes(self.path.read_bytes())
                    os.chmod(backup, 0o600)
                file_descriptor, temporary_name = tempfile.mkstemp(prefix='.notification-', dir=self.path.parent)
                try:
                    with os.fdopen(file_descriptor, 'wb') as temporary:
                        os.fchmod(temporary.fileno(), 0o600)
                        temporary.write(encrypted)
                        temporary.flush()
                        os.fsync(temporary.fileno())
                    os.replace(temporary_name, self.path)
                    os.chmod(self.path, 0o600)
                finally:
                    if os.path.exists(temporary_name):
                        os.unlink(temporary_name)
        except Exception:
            raise

    def update_provider(self, provider: str, values: dict, preserve_empty_secrets=True):
        if provider not in PROVIDER_FIELDS:
            raise NotificationConfigError('unknown notification provider')
        if set(values) - PROVIDER_FIELDS[provider]:
            raise NotificationConfigError('unexpected provider configuration fields')
        payload = self.load()
        current = copy.deepcopy(payload['providers'].get(provider) or {})
        for key, value in values.items():
            if preserve_empty_secrets and key in {'bot_token', 'password'} and value == '':
                continue
            current[key] = value
        current['last_test_ok'] = False
        current['last_test_at'] = None
        payload['providers'][provider] = current
        self.save(payload)
        return current

    def record_test(self, provider: str, succeeded: bool, tested_at: str):
        payload = self.load()
        values = payload['providers'].setdefault(provider, {})
        values['last_test_ok'] = bool(succeeded)
        values['last_test_at'] = tested_at
        self.save(payload)

    def set_enabled(self, provider: str, enabled: bool):
        if provider not in PROVIDER_FIELDS:
            raise NotificationConfigError('unknown notification provider')
        payload = self.load()
        payload['providers'].setdefault(provider, {})['enabled'] = bool(enabled)
        self.save(payload)
