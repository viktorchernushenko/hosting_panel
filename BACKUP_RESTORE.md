# Backup and restore

Managed WordPress site backups include the normal ZIP plus `.wordpress.sql` and `.wordpress.sql.sha256` sidecars. Restore validates the dump before destructive file replacement, then restores files and the isolated database together; retention and deletion remove all artifacts.

Manual WordPress sites use the same backup pipeline. Before destructive restore, the runtime reconciles only the site document root to tenant-scoped group permissions; world-writable modes are never used. A manual site without an attached database remains file-backup capable.

MyH has two deliberately separate stages:

1. A consistent local backup under `/srv/backups/myh/<UTC backup id>`.
2. An encrypted copy to a configured off-server target, followed by a remote SHA-256 comparison.

`LOCAL_BACKUP=verified` never implies `REMOTE_BACKUP=verified`. State is stored root-only in `/var/lib/myh-backup/status.json` and displayed in Admin → Backup Center.

## Configuration

Copy `systemd/myh-backup.conf.example` to `/etc/myh-backup.conf` as `root:root` mode `0600`. Supported targets are `local_mount` and key-authenticated `sftp`. A `local_mount` target must pass `findmnt --mountpoint`, preventing a missing disk from silently writing onto production storage.

Every remote target requires `BACKUP_GPG_RECIPIENT`. Only its public encryption identity is needed on production; escrow the private recovery key away from this server. SFTP uses a dedicated identity and strict host-key checking. Never commit private keys or passwords.

Local retention uses `BACKUP_RETENTION_DAYS` (default 14). Remote retention remains the independent storage policy until a target and its capacity are selected.

## Contents and verification

The job uses the SQLite Backup API, runs `PRAGMA integrity_check`, records key table counts, and excludes live SQLite files from the general archive. It includes site/application data, customer DB backups, SFTP paths, and non-secret SSH/Cloudflare/systemd metadata. Volatile caches, logs and Docker layers are excluded.

Disposable local restore test:

```bash
sudo /home/myserver/hosting_panel/venv/bin/python /home/myserver/hosting_panel/scripts/myh_backup.py --restore-test
```

This verifies SHA-256, opens a copied SQLite DB, checks integrity and compares row counts. A remote restore test remains `WAITING FOR STORAGE` until a real target exists.
