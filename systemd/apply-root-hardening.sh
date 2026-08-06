#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root: sudo bash /home/myserver/hosting_panel/systemd/apply-root-hardening.sh"
  exit 1
fi

backup_dir="/root/myh-rollout-backup-$(date +%F-%H%M%S)"
mkdir -p "$backup_dir"

echo "[1/8] Backing up critical configs to $backup_dir"
cp -a /etc/ssh "$backup_dir/" || true
cp -a /etc/systemd/system/myh-guru.service "$backup_dir/" 2>/dev/null || true
cp -a /etc/hosting-panel.env "$backup_dir/" 2>/dev/null || true
cp -a /etc/nginx "$backup_dir/" 2>/dev/null || true

if [[ -f /etc/hosting-panel.env ]]; then
  echo "[2/8] Rotating HOSTING_PANEL_SECRET"
  cp /etc/hosting-panel.env "/etc/hosting-panel.env.bak.$(date +%F-%H%M%S)"
else
  echo "[2/8] Creating /etc/hosting-panel.env"
fi
echo "HOSTING_PANEL_SECRET=$(openssl rand -hex 48)" > /etc/hosting-panel.env
chmod 600 /etc/hosting-panel.env

echo "[3/8] Applying SSH hardening include"
install -m 644 /home/myserver/hosting_panel/systemd/99-myh-hardening.conf /etc/ssh/sshd_config.d/99-myh-hardening.conf
sshd -t
systemctl reload ssh

echo "[4/8] Restarting hosting panel"
systemctl restart myh-guru
systemctl --no-pager --full status myh-guru | sed -n '1,60p'

echo "[5/8] Reloading nginx (if installed)"
if command -v nginx >/dev/null 2>&1; then
  nginx -t
  systemctl reload nginx
fi

echo "[6/8] Restarting Docker stacks"
if command -v docker >/dev/null 2>&1; then
  if [[ -f /home/myserver/web-lab/docker-compose.yml ]]; then
    cd /home/myserver/web-lab
    docker compose config >/dev/null
    docker compose up -d
  fi
  if [[ -f /home/myserver/nextcloud-docker/docker-compose.yml ]]; then
    cd /home/myserver/nextcloud-docker
    docker compose config >/dev/null
    docker compose up -d
  fi
fi

echo "[7/8] Optional: lock down risky listeners via UFW"
cat <<'EOF'
Review and run manually if needed:
  ufw deny 8082/tcp
  ufw deny 9090/tcp
  ufw deny 631/tcp
  ufw deny 139/tcp
  ufw deny 445/tcp
EOF

echo "[8/8] Done. Rotate cloudflared token and code-server password in their control planes."
