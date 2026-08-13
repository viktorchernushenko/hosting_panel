# MyH

MyH is a production-oriented, multi-user Flask hosting platform. Current release: **2.3.0**. The canonical version is stored in [`VERSION`](VERSION).

## Verified capabilities

- Authentication, RBAC/tenant authorization, CSRF protection and audit logging.
- Site-centred management for files, deployments, domains/SSL, customer databases, backups, logs and settings.
- Backend-driven runtime registry and lifecycle-tested Static, PHP 8.2, Node.js 22, Python 3.12 and validated Dockerfile hosting.
- ZIP/upload and Git deployment workflows, signed GitHub webhooks and deployment history.
- File upload/download/edit/create/rename/move/delete with traversal, symlink and tenant-isolation protections.
- Chrooted `internal-sftp` accounts without shell or forwarding.
- MySQL 8.4 customer databases with per-application credentials, logical backup/checksum/restore; SQLite retained for internal MyH metadata.
- Cloudflare Tunnel ingress, DNS/SSL status and Cloudflare-managed edge certificate renewal.
- Verified local backups, optional encrypted off-server copy, provider-based alerts and operations monitoring.
- Read-only local MyH AI for runtime guidance and sanitized site diagnostics.

See [RUNTIMES.md](RUNTIMES.md), [ARCHITECTURE.md](ARCHITECTURE.md), [SECURITY.md](SECURITY.md), [DATABASES.md](DATABASES.md), [SFTP.md](SFTP.md), [BACKUP_RESTORE.md](BACKUP_RESTORE.md), [AI.md](AI.md) and [OPERATIONS.md](OPERATIONS.md).

## Architecture

Production uses Cloudflare edge and Tunnel in front of Gunicorn. Customer HTTP runtimes bind only to loopback; MySQL binds only to the private Docker database bridge. The application metadata database is SQLite. Secrets live outside Git in root-controlled configuration files.

## Install

Requirements: a supported Linux host with Python, systemd, Docker/Compose, OpenSSH and access to the required DNS/Cloudflare configuration.

```bash
git clone https://github.com/viktorchernushenko/hosting_panel.git
cd hosting_panel
sudo bash install/install-hosting-panel.sh --domain your-domain.com
```

Default generic-install paths are `/opt/hosting-panel`, `hosting-panel.service` and `127.0.0.1:5000`. The current myh.guru production deployment intentionally runs from `/home/myserver/hosting_panel` as documented in [OPERATIONS.md](OPERATIONS.md).

## Development and verification

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -p 'test_*.py'
PYTHONPATH=. python scripts/runtime_smoke.py
```

GitHub Actions performs Python compilation, unit tests and installer shell syntax checks.

## Known limitations

- Off-server backup requires an independently mounted or SFTP target plus a GPG recipient.
- Telegram/SMTP alerts require server-side credentials.
- AI inference is advisory and can be slow on CPU.
- Customer Docker Compose is disabled; validated Dockerfile projects are supported.
- WordPress provisioning exists but is not claimed as lifecycle-verified in the current release.

Never commit `.env` files, credentials, private keys, production databases, user uploads or backup archives.
