# MyH 2.7.0 release notes

Released: 2026-08-13

MyH 2.7.0 adds complete Telegram and standard SMTP configuration to Admin → Сповіщення. Administrators can save credentials, select transport encryption, enable or disable each provider, and run a real provider-specific delivery test without editing server files.

Secrets are authenticated-encrypted on the server, written atomically with restrictive permissions, and never returned to HTML or the settings API. Existing environment configuration remains a compatible fallback; UI-managed configuration is loaded dynamically and needs no service restart.

A provider is not marked configured merely because fields exist. Only a successful real delivery test produces `CONFIGURED`; failed or unverified settings produce `ERROR`, and disabled credentials remain stored without being used for operational alerts.

See [NOTIFICATIONS.md](NOTIFICATIONS.md), [CHANGELOG.md](CHANGELOG.md) and [OPERATIONS.md](OPERATIONS.md).
