# Hosting Panel Server Runbook

This runbook starts and maintains the production service for the hosting panel.

For new servers, use the generic installer guide: `INSTALL_ANY_SERVER.md`.

## 1) Initial deploy

Run as root:

```bash
sudo bash /home/myserver/hosting_panel/systemd/deploy-hosting-panel.sh
```

This command now delegates to the generic installer with production defaults for this host.

What it does:
- Prepares virtual environment and Python dependencies.
- Ensures `/etc/hosting-panel.env` exists.
- Installs `myh-guru.service` from repository template.
- Restarts service and performs local health check.
- Reloads nginx config if nginx is installed.

## 2) Daily operations

Service status:

```bash
sudo systemctl status myh-guru --no-pager
```

Restart service:

```bash
sudo systemctl restart myh-guru
```

Tail logs:

```bash
sudo journalctl -u myh-guru -f
```

## 3) Health checks

Local app:

```bash
curl -sS -o /dev/null -w 'local_panel=%{http_code}\n' http://127.0.0.1:5000
```

Public endpoint:

```bash
curl -sS -o /dev/null -w 'public_myh=%{http_code}\n' https://myh.guru
```

## 4) Secret rotation

Rotate panel secret and restart:

```bash
echo "HOSTING_PANEL_SECRET=$(openssl rand -hex 48)" | sudo tee /etc/hosting-panel.env >/dev/null
sudo chmod 600 /etc/hosting-panel.env
sudo systemctl restart myh-guru
```

## 5) Rollback

Use service logs and recover previous unit/env backups from your existing backup workflow.
