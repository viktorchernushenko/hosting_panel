#!/usr/bin/env bash
set -euo pipefail

PANEL_SERVICE="${PANEL_SERVICE:-myh-guru}"
PANEL_HEALTH_URL="${PANEL_HEALTH_URL:-http://127.0.0.1:5000/healthz}"

log() {
  logger -t myh-watchdog "$1"
  echo "$1"
}

unit_exists() {
  local service="$1"
  systemctl cat "$service" >/dev/null 2>&1
}

check_and_restart() {
  local service="$1"
  if ! unit_exists "$service"; then
    return
  fi
  if ! systemctl is-active --quiet "$service"; then
    log "service down: $service; restarting"
    systemctl restart "$service" || log "restart failed: $service"
  fi
}

check_panel_health() {
  if ! command -v curl >/dev/null 2>&1; then
    log "curl missing; skipping HTTP health check"
    return
  fi
  if ! curl -fsS --max-time 8 "$PANEL_HEALTH_URL" | grep -q '"ok"[[:space:]]*:[[:space:]]*true'; then
    log "panel health check failed: $PANEL_HEALTH_URL; restarting $PANEL_SERVICE"
    systemctl restart "$PANEL_SERVICE" || log "restart failed: $PANEL_SERVICE"
    if curl -fsS --max-time 8 "$PANEL_HEALTH_URL" | grep -q '"ok"[[:space:]]*:[[:space:]]*true'; then
      log "panel health recovered after restart"
    else
      log "panel still unhealthy after restart"
    fi
  fi
}

check_and_restart "$PANEL_SERVICE"
check_panel_health
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
