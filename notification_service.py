"""Server-side notification providers with safe results and alert deduplication."""
from __future__ import annotations

import json
import os
import smtplib
import ssl
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path


@dataclass
class DeliveryResult:
    provider: str
    configured: bool
    delivered: bool
    detail: str


class TelegramProvider:
    name = 'telegram'

    def __init__(self, enabled: bool, token: str, chat_id: str):
        self.enabled, self.token, self.chat_id = enabled, token, chat_id

    @property
    def configured(self):
        return self.enabled and bool(self.token and self.chat_id)

    def send(self, subject: str, message: str) -> DeliveryResult:
        if not self.configured:
            return DeliveryResult(self.name, False, False, 'not configured')
        try:
            body = urllib.parse.urlencode({'chat_id': self.chat_id, 'text': f'{subject}\n{message}'}).encode()
            request = urllib.request.Request(
                f'https://api.telegram.org/bot{self.token}/sendMessage', data=body, method='POST'
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                if response.status >= 300:
                    raise RuntimeError(f'provider returned HTTP {response.status}')
            return DeliveryResult(self.name, True, True, 'delivered')
        except Exception:
            return DeliveryResult(self.name, True, False, 'provider request failed')


class SMTPProvider:
    name = 'smtp'

    def __init__(self, enabled: bool, host: str, port: int, username: str, password: str,
                 sender: str, recipients: list[str], use_tls: bool):
        self.enabled, self.host, self.port = enabled, host, port
        self.username, self.password, self.sender = username, password, sender
        self.recipients, self.use_tls = recipients, use_tls

    @property
    def configured(self):
        return self.enabled and bool(self.host and self.sender and self.recipients)

    def send(self, subject: str, message: str) -> DeliveryResult:
        if not self.configured:
            return DeliveryResult(self.name, False, False, 'not configured')
        mail = EmailMessage()
        mail['Subject'], mail['From'], mail['To'] = subject, self.sender, ', '.join(self.recipients)
        mail.set_content(message)
        try:
            with smtplib.SMTP(self.host, self.port, timeout=10) as client:
                if self.use_tls:
                    client.starttls(context=ssl.create_default_context())
                if self.username:
                    client.login(self.username, self.password)
                client.send_message(mail)
            return DeliveryResult(self.name, True, True, 'delivered')
        except Exception:
            return DeliveryResult(self.name, True, False, 'provider request failed')


class NotificationService:
    def __init__(self, providers, state_path: str, cooldown_seconds: int = 3600):
        self.providers = providers
        self.state_path = Path(state_path)
        self.cooldown_seconds = max(60, cooldown_seconds)

    @classmethod
    def from_env(cls, state_path: str):
        enabled = lambda name: os.environ.get(name, '').lower() in {'1', 'true', 'yes', 'on'}
        recipients = [item.strip() for item in os.environ.get('SMTP_RECIPIENTS', os.environ.get('SMTP_TO', '')).split(',') if item.strip()]
        return cls([
            TelegramProvider(enabled('TELEGRAM_ENABLED'), os.environ.get('TELEGRAM_BOT_TOKEN', ''), os.environ.get('TELEGRAM_CHAT_ID', '')),
            SMTPProvider(
                enabled('SMTP_ENABLED'), os.environ.get('SMTP_HOST', ''), int(os.environ.get('SMTP_PORT', '587')),
                os.environ.get('SMTP_USERNAME', os.environ.get('SMTP_USER', '')), os.environ.get('SMTP_PASSWORD', ''),
                os.environ.get('SMTP_FROM', os.environ.get('SMTP_USER', '')), recipients,
                enabled('SMTP_TLS') if 'SMTP_TLS' in os.environ else True,
            ),
        ], state_path, int(os.environ.get('ALERT_COOLDOWN_SECONDS', '3600')))

    def statuses(self):
        return [{'provider': item.name, 'configured': item.configured} for item in self.providers]

    def _state(self):
        try:
            return json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return {}

    def _save_state(self, state):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, sort_keys=True))
        os.chmod(temporary, 0o600)
        temporary.replace(self.state_path)

    def send(self, message: str, level='info', alert_key=None, resolved=False, force=False):
        now, state = int(time.time()), self._state()
        key = str(alert_key or '')[:160]
        previous = state.get(key, {}) if key else {}
        if key and not force:
            if resolved and not previous:
                return {'delivered': False, 'suppressed': True, 'results': []}
            same_state = bool(previous.get('resolved')) == bool(resolved)
            if same_state and now - int(previous.get('sent_at', 0)) < self.cooldown_seconds:
                return {'delivered': False, 'suppressed': True, 'results': []}
        subject = f'[MyH {"RECOVERED" if resolved else level.upper()}]'
        results = [provider.send(subject, message[:2000]) for provider in self.providers]
        delivered = any(item.delivered for item in results)
        if key and (delivered or not any(item.configured for item in self.providers)):
            state[key] = {'sent_at': now, 'resolved': bool(resolved)}
            self._save_state(state)
        return {
            'delivered': delivered, 'suppressed': False,
            'results': [item.__dict__ for item in results],
        }
