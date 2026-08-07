#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_SOURCE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

INSTALL_DIR="/opt/hosting-panel"
APP_USER="hostingpanel"
APP_GROUP="hostingpanel"
SERVICE_NAME="hosting-panel"
DOMAIN="localhost"
BIND_HOST="127.0.0.1"
BIND_PORT="5000"
ENV_FILE="/etc/hosting-panel.env"
SOURCE_DIR="$DEFAULT_SOURCE_DIR"
INSTALL_NGINX=1

usage() {
  cat <<'USAGE'
Usage:
  sudo bash install-hosting-panel.sh [options]

Options:
  --install-dir PATH       Target install path (default: /opt/hosting-panel)
  --source-dir PATH        Source directory containing app.py (default: repo root)
  --user NAME              System user to run service (default: hostingpanel)
  --group NAME             System group to run service (default: hostingpanel)
  --service-name NAME      systemd unit name without .service (default: hosting-panel)
  --domain DOMAIN          Public domain for nginx config (default: localhost)
  --bind-host HOST         Gunicorn bind host (default: 127.0.0.1)
  --bind-port PORT         Gunicorn bind port (default: 5000)
  --env-file PATH          Environment file path (default: /etc/hosting-panel.env)
  --no-nginx               Skip nginx vhost setup
  --help                   Show this help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-dir) INSTALL_DIR="$2"; shift 2 ;;
    --source-dir) SOURCE_DIR="$2"; shift 2 ;;
    --user) APP_USER="$2"; shift 2 ;;
    --group) APP_GROUP="$2"; shift 2 ;;
    --service-name) SERVICE_NAME="$2"; shift 2 ;;
    --domain) DOMAIN="$2"; shift 2 ;;
    --bind-host) BIND_HOST="$2"; shift 2 ;;
    --bind-port) BIND_PORT="$2"; shift 2 ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --no-nginx) INSTALL_NGINX=0; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown argument: $1"; usage; exit 1 ;;
  esac
done

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root: sudo bash $0"
  exit 1
fi

if [[ ! -f "$SOURCE_DIR/app.py" ]]; then
  echo "Missing app.py in source dir: $SOURCE_DIR"
  exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemd is required (systemctl not found)."
  exit 1
fi

if command -v apt-get >/dev/null 2>&1; then
  echo "[1/10] Installing OS dependencies via apt"
  DEBIAN_FRONTEND=noninteractive apt-get update -y
  DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip curl
else
  echo "[1/10] Skipping OS dependency install (apt-get not found)."
  echo "       Ensure python3, python3-venv, python3-pip and curl are installed."
fi

echo "[2/10] Ensuring service user and group"
if ! getent group "$APP_GROUP" >/dev/null; then
  groupadd --system "$APP_GROUP"
fi
if ! id -u "$APP_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "/var/lib/$APP_USER" --gid "$APP_GROUP" --shell /usr/sbin/nologin "$APP_USER"
fi

echo "[3/10] Syncing application files"
mkdir -p "$INSTALL_DIR"
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete \
    --exclude '.git' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude 'venv' \
    --exclude '.venv' \
    --exclude 'instance/hosting.db' \
    "$SOURCE_DIR/" "$INSTALL_DIR/"
else
  cp -a "$SOURCE_DIR/." "$INSTALL_DIR/"
  rm -rf "$INSTALL_DIR/.git" "$INSTALL_DIR/.venv" "$INSTALL_DIR/venv" || true
fi
chown -R "$APP_USER:$APP_GROUP" "$INSTALL_DIR"

echo "[4/10] Preparing runtime directories"
mkdir -p "$INSTALL_DIR/instance" "$INSTALL_DIR/user_sites"
chown -R "$APP_USER:$APP_GROUP" "$INSTALL_DIR/instance" "$INSTALL_DIR/user_sites"

echo "[5/10] Building Python virtual environment"
if [[ ! -x "$INSTALL_DIR/venv/bin/python" ]]; then
  sudo -u "$APP_USER" python3 -m venv "$INSTALL_DIR/venv"
fi
sudo -u "$APP_USER" "$INSTALL_DIR/venv/bin/pip" install --upgrade pip >/dev/null
sudo -u "$APP_USER" "$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

echo "[6/10] Ensuring environment file"
if [[ ! -f "$ENV_FILE" ]]; then
  umask 077
  cat > "$ENV_FILE" <<ENV
HOSTING_PANEL_SECRET=$(openssl rand -hex 48)
HOSTING_PANEL_VERSION=1.6.0
ENV
fi
chmod 600 "$ENV_FILE"
if ! grep -q '^HOSTING_PANEL_SFTP_PROVISION_SERVICE=' "$ENV_FILE"; then
  echo 'HOSTING_PANEL_SFTP_PROVISION_SERVICE=myh-sftp-provision.service' >> "$ENV_FILE"
fi
if ! grep -q '^HOSTING_PANEL_SFTP_PROVISION_WORKER=' "$ENV_FILE"; then
  echo 'HOSTING_PANEL_SFTP_PROVISION_WORKER=/usr/local/sbin/myh-sftp-provision-worker.sh' >> "$ENV_FILE"
fi

echo "[7/10] Writing systemd unit"
UNIT_FILE="/etc/systemd/system/$SERVICE_NAME.service"
cat > "$UNIT_FILE" <<UNIT
[Unit]
Description=Hosting Panel ($SERVICE_NAME)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_GROUP
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$ENV_FILE
Environment=TMPDIR=/run/$SERVICE_NAME
RuntimeDirectory=$SERVICE_NAME
RuntimeDirectoryMode=0755
ExecStart=$INSTALL_DIR/venv/bin/gunicorn --workers 1 --threads 4 --bind $BIND_HOST:$BIND_PORT --worker-tmp-dir /run/$SERVICE_NAME --timeout 30 --access-logfile - --error-logfile - app:app
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
UMask=0077
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$INSTALL_DIR/instance $INSTALL_DIR/user_sites
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
ProtectKernelLogs=true
ProtectClock=true
ProtectHostname=true
RestrictSUIDSGID=true
RestrictRealtime=true
RestrictNamespaces=true
LockPersonality=true
MemoryDenyWriteExecute=true
CapabilityBoundingSet=
SystemCallArchitectures=native
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
RemoveIPC=true

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable "$SERVICE_NAME.service"

echo "[8/13] Installing SFTP root provisioner"
SFTP_WORKER_SRC="$INSTALL_DIR/systemd/myh-sftp-provision-worker.sh"
SFTP_WORKER_DST="/usr/local/sbin/myh-sftp-provision-worker.sh"
SFTP_UNIT_SRC="$INSTALL_DIR/systemd/myh-sftp-provision.service"
SFTP_UNIT_DST="/etc/systemd/system/myh-sftp-provision.service"
if [[ -f "$SFTP_WORKER_SRC" && -f "$SFTP_UNIT_SRC" ]]; then
  install -m 700 "$SFTP_WORKER_SRC" "$SFTP_WORKER_DST"
  install -m 644 "$SFTP_UNIT_SRC" "$SFTP_UNIT_DST"
  systemctl daemon-reload
  systemctl enable myh-sftp-provision.service
  SYSTEMCTL_BIN="$(command -v systemctl)"
  SUDOERS_FILE="/etc/sudoers.d/90-hosting-panel-sftp-provision"
  {
    echo "${APP_USER} ALL=(root) NOPASSWD: ${SYSTEMCTL_BIN} start myh-sftp-provision.service"
  } > "$SUDOERS_FILE"
  chmod 440 "$SUDOERS_FILE"
  visudo -cf "$SUDOERS_FILE" >/dev/null
fi

echo "[9/13] Starting service"
systemctl restart "$SERVICE_NAME.service"
systemctl --no-pager --full status "$SERVICE_NAME.service" | sed -n '1,30p'

echo "[10/13] Installing watchdog"
WATCHDOG_SCRIPT="/usr/local/sbin/${SERVICE_NAME}-watchdog.sh"
WATCHDOG_SERVICE="/etc/systemd/system/${SERVICE_NAME}-watchdog.service"
WATCHDOG_TIMER="/etc/systemd/system/${SERVICE_NAME}-watchdog.timer"
cat > "$WATCHDOG_SCRIPT" <<WATCHDOG
#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="$SERVICE_NAME"
HEALTH_URL="http://$BIND_HOST:$BIND_PORT/healthz"

log() {
  logger -t "
${SERVICE_NAME}-watchdog" "\$1"
}

if ! systemctl is-active --quiet "\$SERVICE_NAME"; then
  log "service down: \$SERVICE_NAME; restarting"
  systemctl restart "\$SERVICE_NAME" || log "restart failed: \$SERVICE_NAME"
fi

if command -v curl >/dev/null 2>&1; then
  if ! curl -fsS --max-time 8 "\$HEALTH_URL" | grep -q '"ok"[[:space:]]*:[[:space:]]*true'; then
    log "health check failed: \$HEALTH_URL; restarting \$SERVICE_NAME"
    systemctl restart "\$SERVICE_NAME" || log "restart failed: \$SERVICE_NAME"
  fi
fi
WATCHDOG
chmod 755 "$WATCHDOG_SCRIPT"

cat > "$WATCHDOG_SERVICE" <<WATCHDOG_SVC
[Unit]
Description=Hosting Panel Watchdog ($SERVICE_NAME)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=$WATCHDOG_SCRIPT
User=root
Group=root
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/log
WATCHDOG_SVC

cat > "$WATCHDOG_TIMER" <<WATCHDOG_TMR
[Unit]
Description=Run watchdog for $SERVICE_NAME every 2 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=2min
Unit=$(basename "$WATCHDOG_SERVICE")
Persistent=true

[Install]
WantedBy=timers.target
WATCHDOG_TMR

systemctl daemon-reload
systemctl enable --now "$(basename "$WATCHDOG_TIMER")"

echo "[11/13] Configuring nginx (optional)"
if [[ "$INSTALL_NGINX" -eq 1 ]] && command -v nginx >/dev/null 2>&1; then
  NGINX_CONF="/etc/nginx/sites-available/$SERVICE_NAME.conf"
  SERVER_NAMES="$DOMAIN"
  if [[ "$DOMAIN" != "localhost" && "$DOMAIN" != "127.0.0.1" ]]; then
    SERVER_NAMES="$DOMAIN www.$DOMAIN"
  fi
  cat > "$NGINX_CONF" <<NGINX
server {
    listen 80;
    server_name $SERVER_NAMES localhost;

    location / {
        proxy_pass http://$BIND_HOST:$BIND_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
NGINX
  ln -sfn "$NGINX_CONF" "/etc/nginx/sites-enabled/$SERVICE_NAME.conf"
  nginx -t
  systemctl reload nginx
else
  echo "nginx skipped"
fi

echo "[12/13] Installing admin helper command"
cat > /usr/local/bin/admin <<ADMIN
#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$INSTALL_DIR"
APP_USER="$APP_USER"
ENV_FILE="$ENV_FILE"
PYTHON_BIN="\$APP_DIR/venv/bin/python"

if [[ ! -x "\$PYTHON_BIN" ]]; then
  echo "Missing runtime python: \$PYTHON_BIN"
  exit 1
fi

USERNAME="\${1:-developer}"
PASSWORD="\${2:-\$(openssl rand -hex 12)}"

if [[ ! -e "$ENV_FILE" ]]; then
  echo "Missing env file: $ENV_FILE"
  exit 1
fi

if ! sudo test -r "$ENV_FILE"; then
  echo "Cannot read env file as root: $ENV_FILE"
  exit 1
fi

sudo sh -c "set -a; . '\$ENV_FILE'; set +a; cd '\$APP_DIR'; '\$PYTHON_BIN' app.py --create-admin --username '\$USERNAME' --password '\$PASSWORD' --force"

echo "admin_username=\$USERNAME"
echo "admin_password=\$PASSWORD"
ADMIN
chmod 755 /usr/local/bin/admin

echo "[13/13] Health check"
HTTP_CODE="$(curl -sS -o /dev/null -w "%{http_code}" "http://$BIND_HOST:$BIND_PORT/healthz" || true)"
echo "local_healthz=$HTTP_CODE"
if [[ "$HTTP_CODE" != "200" ]]; then
  echo "Panel health check failed"
  exit 1
fi

echo "Done"
echo "Service: $SERVICE_NAME.service"
echo "Install dir: $INSTALL_DIR"
echo "Env file: $ENV_FILE"
echo "Admin helper: /usr/local/bin/admin"
echo "Watchdog timer: $(basename "$WATCHDOG_TIMER")"
echo "Logs: journalctl -u $SERVICE_NAME.service -f"
