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
                 sender: str, recipients: list[str], encryption: str):
        self.enabled, self.host, self.port = enabled, host, port
        self.username, self.password, self.sender = username, password, sender
        self.recipients, self.encryption = recipients, encryption

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
            client_class = smtplib.SMTP_SSL if self.encryption == 'ssl' else smtplib.SMTP
            client_kwargs = {'context': ssl.create_default_context()} if self.encryption == 'ssl' else {}
            with client_class(self.host, self.port, timeout=10, **client_kwargs) as client:
                if self.encryption == 'tls':
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
    def from_env(cls, state_path: str, stored_settings=None):
        enabled = lambda name: os.environ.get(name, '').lower() in {'1', 'true', 'yes', 'on'}
        stored_settings = stored_settings or {}
        telegram = stored_settings.get('telegram') or {}
        smtp = stored_settings.get('smtp') or {}
        recipients_value = smtp.get('recipients', os.environ.get('SMTP_RECIPIENTS', os.environ.get('SMTP_TO', '')))
        if isinstance(recipients_value, list):
            recipients = recipients_value
        else:
            recipients = [item.strip() for item in str(recipients_value).split(',') if item.strip()]
        env_encryption = 'tls' if ('SMTP_TLS' not in os.environ or enabled('SMTP_TLS')) else 'none'
        return cls([
            TelegramProvider(
                bool(telegram.get('enabled')) if telegram else enabled('TELEGRAM_ENABLED'),
                telegram.get('bot_token', os.environ.get('TELEGRAM_BOT_TOKEN', '')),
                telegram.get('chat_id', os.environ.get('TELEGRAM_CHAT_ID', '')),
            ),
            SMTPProvider(
                bool(smtp.get('enabled')) if smtp else enabled('SMTP_ENABLED'),
                smtp.get('host', os.environ.get('SMTP_HOST', '')),
                int(smtp.get('port', os.environ.get('SMTP_PORT', '587'))),
                smtp.get('username', os.environ.get('SMTP_USERNAME', os.environ.get('SMTP_USER', ''))),
                smtp.get('password', os.environ.get('SMTP_PASSWORD', '')),
                smtp.get('sender', os.environ.get('SMTP_FROM', os.environ.get('SMTP_USER', ''))), recipients,
                smtp.get('encryption', env_encryption),
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

    def send(self, message: str, level='info', alert_key=None, resolved=False, force=False, provider_name=None):
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
        selected = [provider for provider in self.providers if not provider_name or provider.name == provider_name]
        results = [provider.send(subject, message[:2000]) for provider in selected]
        delivered = any(item.delivered for item in results)
        if key and (delivered or not any(item.configured for item in self.providers)):
            state[key] = {'sent_at': now, 'resolved': bool(resolved)}
            self._save_state(state)
        return {
            'delivered': delivered, 'suppressed': False,
            'results': [item.__dict__ for item in results],
        }
