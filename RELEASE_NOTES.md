# MyH 2.6.0 release notes

Released: 2026-08-13

MyH 2.6.0 makes the administrative dashboard operationally truthful. “Потребує уваги” is generated from one backend model and includes only findings that require action; healthy Cloudflare TLS and SQLite integrity are shown separately under platform status.

The audit confirms that local backups are current but no independently hosted backup target is configured, and that neither Telegram nor SMTP has complete server-side configuration. Both remain visible actions rather than optimistic successes.

The live SFTP inventory contains one panel-linked production account and four unlinked accounts. Those four remain `UNKNOWN`, are explicitly not safe to remove, and were not modified or deleted.

See [BACKUP.md](BACKUP.md), [NOTIFICATIONS.md](NOTIFICATIONS.md), [SFTP.md](SFTP.md), [CHANGELOG.md](CHANGELOG.md) and [OPERATIONS.md](OPERATIONS.md).
