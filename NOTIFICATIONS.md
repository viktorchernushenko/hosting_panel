# External notifications

`NotificationService` provides independent Telegram and SMTP delivery. Missing providers return `not configured`; success is never fabricated. Alert keys have a configurable cooldown and recovery transition. Administrators normally configure both providers in Admin → Сповіщення.

UI-managed credentials are stored in `instance/notification_config.enc` with authenticated encryption and mode `0600`. The encryption key is derived from the existing `HOSTING_PANEL_SECRET`; the key and plaintext credentials are not stored in that file. Writes use a process lock, encrypted backup and atomic replacement. Saved secrets are never returned to the browser.

Existing `/etc/hosting-panel.env` settings remain supported as a deployment fallback (mode `0600`):

- Telegram: `TELEGRAM_ENABLED`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
- SMTP: `SMTP_ENABLED`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_TLS`, `SMTP_RECIPIENTS`.
- Deduplication: `ALERT_COOLDOWN_SECONDS` (default 3600).

Do not commit or log credentials. The Admin UI sends credentials only in HTTPS POST bodies, preserves an existing secret when its edit field is blank, and exposes `Не налаштовано`, `Налаштовано`, `Помилка / не перевірено`, or `Вимкнено`. A provider becomes `Налаштовано` only after its dedicated real-delivery test succeeds.

The watchdog covers panel health, failed services, off-server backup status/staleness and Cloudflare edge TLS expiry.
