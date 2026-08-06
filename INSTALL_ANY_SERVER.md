# Hosting Panel Installer (Any Server)

This guide installs the hosting panel on a fresh Linux server using one script.

## 1) Prepare source code

Clone or copy this repository to the server, then go to the project directory.

## 2) Run installer

```bash
cd hosting_panel
sudo bash install/install-hosting-panel.sh \
  --domain your-domain.com \
  --install-dir /opt/hosting-panel \
  --service-name hosting-panel
```

## 3) What installer does

- Creates dedicated system user/group.
- Copies app files to install directory.
- Creates Python virtual environment and installs dependencies.
- Creates or reuses environment file with secure secret.
- Generates hardened systemd service unit.
- Optionally configures nginx reverse proxy if nginx is present.
- Installs global `admin` helper command in `/usr/local/bin/admin`.
- Starts service and performs local health check.

## 4) Verify

```bash
sudo systemctl status hosting-panel --no-pager
curl -sS -o /dev/null -w 'local_panel=%{http_code}\n' http://127.0.0.1:5000
```

## 5) First admin account

Use the global helper command:

```bash
sudo admin developer ChangeMe123456
```

Or use the built-in CLI command directly:

```bash
cd /opt/hosting-panel
sudo -u hostingpanel ./venv/bin/python app.py --create-admin --username developer --password "ChangeMe123456" --force
```

Note: avoid `!` in shell passwords unless escaped.

## 6) Reinstall/upgrade

Run the same installer again with the same parameters. It syncs files and restarts the service.

## 7) Useful flags

```bash
sudo bash install/install-hosting-panel.sh --help
```

Key flags:
- `--source-dir`
- `--install-dir`
- `--user`
- `--group`
- `--service-name`
- `--domain`
- `--bind-host`
- `--bind-port`
- `--env-file`
- `--no-nginx`
