#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALLER="$APP_DIR/install/install-hosting-panel.sh"

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root: sudo bash $APP_DIR/systemd/deploy-hosting-panel.sh"
  exit 1
fi

if [[ ! -x "$INSTALLER" ]]; then
  chmod +x "$INSTALLER"
fi

exec bash "$INSTALLER" \
  --source-dir "$APP_DIR" \
  --install-dir "/home/myserver/hosting_panel" \
  --user "myserver" \
  --group "myserver" \
  --service-name "myh-guru" \
  --domain "myh.guru"
