# External notifications

`NotificationService` provides independent Telegram and SMTP delivery. Missing providers return `not configured`; success is never fabricated. Alert keys have a configurable cooldown and recovery transition.

Configure secrets only in `/etc/hosting-panel.env` (mode `0600`):

- Telegram: `TELEGRAM_ENABLED`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
- SMTP: `SMTP_ENABLED`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_TLS`, `SMTP_RECIPIENTS`.
- Deduplication: `ALERT_COOLDOWN_SECONDS` (default 3600).

Do not send credentials through the panel or commit/log them. Admin → Notifications exposes only Configured / Not configured and performs a real delivery test.

The watchdog covers panel health, failed services, off-server backup status/staleness and Cloudflare edge TLS expiry.
