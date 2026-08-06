#!/usr/bin/env bash
set -euo pipefail

backup_root=/mnt/myh-backup
if ! mountpoint -q "$backup_root"; then
    logger -t myh-backup "Backup skipped: $backup_root is not a mounted external filesystem"
    exit 0
fi

stamp=$(date +%Y-%m-%d_%H-%M-%S)
destination="$backup_root/$stamp"
mkdir -p "$destination"

/home/myserver/hosting_panel/venv/bin/python3 - "$destination/hosting.db" <<'PY'
import sqlite3
import sys

source = sqlite3.connect('/home/myserver/hosting_panel/instance/hosting.db')
target = sqlite3.connect(sys.argv[1])
with target:
    source.backup(target)
target.close()
source.close()
PY

tar --xattrs --acls -czf "$destination/hosting-panel.tar.gz" \
    --exclude=venv --exclude=.venv --exclude=__pycache__ \
    -C /home/myserver hosting_panel/user_sites hosting_panel/templates hosting_panel/app.py hosting_panel/requirements.txt

tar --xattrs --acls -czf "$destination/system-config.tar.gz" \
    /etc/ssh /etc/ufw /etc/systemd/system/myh-guru.service /etc/cloudflared /etc/cups /etc/samba

for volume in nextcloud-docker_nextcloud_data web-lab_db_data; do
    path=$(docker volume inspect -f '{{ .Mountpoint }}' "$volume" 2>/dev/null || true)
    if [ -n "$path" ] && [ -d "$path" ]; then
        tar -czf "$destination/${volume}.tar.gz" -C "$path" .
    fi
done

sha256sum "$destination"/* > "$destination/SHA256SUMS"
find "$backup_root" -mindepth 1 -maxdepth 1 -type d -mtime +14 -exec rm -rf -- {} +
logger -t myh-backup "Backup completed: $destination"
