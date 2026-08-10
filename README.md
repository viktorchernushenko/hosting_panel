# Hosting Panel

Production-oriented Flask hosting panel with developer controls, deployment workflows, and one-command server installation.

## Features

- User and site management (developer and user roles)
- Deploy workflows (archive and Git)
- Deployment history and webhook trigger support
- DNS and SSL management integrations
- Service diagnostics, logs, and platform overview
- Public homepage visibility controls managed from developer panel
- Hardened systemd unit templates and backup/watchdog scripts
- Secure-by-default webhook controls (signed GitHub webhook, protected notify webhook)
- Approved service templates under /srv/templates for WordPress, static, Node, Python, and PHP

## Quick Install (Any Server)

```bash
git clone https://github.com/viktorchernushenko/hosting_panel.git
cd hosting_panel
sudo bash install/install-hosting-panel.sh --domain your-domain.com
```

Default install:
- App dir: `/opt/hosting-panel`
- Service: `hosting-panel.service`
- Bind: `127.0.0.1:5000`

## Create or Reset Admin

The installer provides global helper command:

```bash
sudo admin developer ChangeMe123456
```

Direct CLI alternative:

```bash
cd /opt/hosting-panel
sudo -u hostingpanel ./venv/bin/python app.py --create-admin --username developer --password "ChangeMe123456" --force
```

## Runbook

- Generic installation guide: [INSTALL_ANY_SERVER.md](INSTALL_ANY_SERVER.md)
- Ops runbook: [README_SERVER.md](README_SERVER.md)
- Server architecture: [SERVER_ARCHITECTURE.md](SERVER_ARCHITECTURE.md)
- Control panel inventory: [CONTROL_PANEL.md](CONTROL_PANEL.md)
- Deployment notes: [DEPLOYMENT.md](DEPLOYMENT.md)
- Backup notes: [BACKUP.md](BACKUP.md)
- Security notes: [SECURITY.md](SECURITY.md)

## Local Development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

## CI

GitHub Actions validates:

- Python compile step
- Unit tests
- Installer shell syntax

See workflow: [.github/workflows/ci.yml](.github/workflows/ci.yml)

## Security Note

Do not commit production secrets. Keep `/etc/hosting-panel.env` private on target servers.

Recommended webhook settings in environment:

- `HOSTING_PANEL_NOTIFY_WEBHOOK_SECRET`
- `HOSTING_PANEL_ALLOW_INSECURE_NOTIFY_WEBHOOK=0`
- `HOSTING_PANEL_ALLOW_UNSIGNED_GITHUB_WEBHOOK=0`
