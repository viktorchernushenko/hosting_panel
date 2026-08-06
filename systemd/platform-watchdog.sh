#!/usr/bin/env bash
set -euo pipefail

log() {
  logger -t myh-watchdog "$1"
  echo "$1"
}

check_and_restart() {
  local service="$1"
  if ! systemctl is-active --quiet "$service"; then
    log "service down: $service; restarting"
    systemctl restart "$service" || log "restart failed: $service"
  fi
}

check_and_restart myh-guru
check_and_restart cloudflared

if command -v docker >/dev/null 2>&1; then
  restarting="$(docker ps --filter status=restarting --format '{{.Names}}' || true)"
  if [[ -n "$restarting" ]]; then
    log "docker restarting containers detected: $(echo "$restarting" | tr '\n' ' ')"
  fi
fi

failed_units="$(systemctl --no-pager --plain --type=service --state=failed --no-legend | awk '{print $1}' | tr '\n' ' ' || true)"
if [[ -n "${failed_units// /}" ]]; then
  log "failed services detected: $failed_units"
fi
