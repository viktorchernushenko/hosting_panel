#!/usr/bin/env bash
set -euo pipefail

PANEL_SERVICE="${PANEL_SERVICE:-myh-guru}"
PANEL_HEALTH_URL="${PANEL_HEALTH_URL:-http://127.0.0.1:5000/healthz}"
ALERT_COMMAND=(/usr/bin/python3 /usr/local/lib/myh-ops/send_alert.py)

log() {
  logger -t myh-watchdog "$1"
  echo "$1"
}

alert() {
  local key="$1" level="$2" message="$3"
  "${ALERT_COMMAND[@]}" --key "$key" --level "$level" "$message" >/dev/null || true
}

resolved() {
  local key="$1" message="$2"
  "${ALERT_COMMAND[@]}" --key "$key" --resolved "$message" >/dev/null || true
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
      alert panel-health danger "MyH remains unavailable after an automatic restart."
    fi
  else
    resolved panel-health "MyH health check recovered."
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
  alert failed-services danger "MyH detected failed system services."
else
  resolved failed-services "System services recovered."
fi

if [[ -r /var/lib/myh-backup/status.json ]]; then
  backup_state="$(/usr/bin/python3 - <<'PY'
import json, time
p=json.load(open('/var/lib/myh-backup/status.json'))
age=int(time.time())-int(__import__('datetime').datetime.fromisoformat(p['timestamp']).timestamp())
print(p.get('remote_status','unknown'), age)
PY
)"
  read -r remote_status backup_age <<<"$backup_state"
  if [[ "$remote_status" != verified ]]; then
    log "off-server backup not verified: $remote_status"
    alert offserver-backup danger "Off-server backup is not verified. Current status: $remote_status."
  elif (( backup_age > 129600 )); then
    log "off-server backup is stale"
    alert offserver-backup danger "Last verified off-server backup is older than 36 hours."
  else
    resolved offserver-backup "Off-server backup verification recovered."
  fi
fi

tls_days="$(timeout 12 openssl s_client -connect myh.guru:443 -servername myh.guru </dev/null 2>/dev/null | openssl x509 -noout -checkend $((30*86400)) >/dev/null 2>&1; echo $?)"
if [[ "$tls_days" != 0 ]]; then
  log "Cloudflare edge certificate expires within 30 days or validation failed"
  alert edge-tls warning "Cloudflare edge certificate expires within 30 days or could not be validated."
else
  resolved edge-tls "Cloudflare edge TLS has more than 30 days remaining."
fi
