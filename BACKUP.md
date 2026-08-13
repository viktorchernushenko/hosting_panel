# Backup overview

MyH separates site/customer-database restore points from platform disaster-recovery backups.

- Site archives are scoped to one application and support checksum-aware download, restore and deletion.
- Customer MySQL backups use logical `mysqldump`, SHA-256 sidecars and fail-closed restore verification.
- Platform backup creates a consistent local set containing SQLite via its Backup API, application data, customer DB backups and non-secret configuration metadata.
- An off-server stage requires an independently mounted target or key-authenticated SFTP plus GPG encryption. It is currently **not configured** and local success is never presented as remote success.

See [BACKUP_RESTORE.md](BACKUP_RESTORE.md) for configuration, retention and restore-test commands. A copy on the same host is not disaster recovery.
