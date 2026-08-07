#!/usr/bin/env bash
set -euo pipefail

QUEUE_FILE="${HOSTING_PANEL_SFTP_PROVISION_QUEUE_FILE:-/home/myserver/hosting_panel/instance/sftp_provision_queue.jsonl}"
RESULT_FILE="${HOSTING_PANEL_SFTP_PROVISION_RESULT_FILE:-/home/myserver/hosting_panel/instance/sftp_provision_result.jsonl}"
STATE_FILE="${HOSTING_PANEL_SFTP_PROVISION_STATE_FILE:-/var/lib/myh-sftp-provision/state.json}"
LOCK_FILE="${HOSTING_PANEL_SFTP_PROVISION_LOCK_FILE:-/run/myh-sftp-provision.lock}"
CONFIG_FILE="${HOSTING_PANEL_SFTP_CONFIG_FILE:-/etc/ssh/sshd_config.d/99-myh-sftp.conf}"
KEYS_DIR="${HOSTING_PANEL_SFTP_KEYS_DIR:-/etc/ssh/myh-sftp-authorized-keys}"
SFTP_GROUP="${HOSTING_PANEL_SFTP_GROUP:-myh_sftp}"

umask 077
mkdir -p "$(dirname "$QUEUE_FILE")" "$(dirname "$RESULT_FILE")" "$(dirname "$STATE_FILE")" "$(dirname "$CONFIG_FILE")" "$KEYS_DIR"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  exit 0
fi

if [[ ! -s "$QUEUE_FILE" ]]; then
  exit 0
fi

: > "$RESULT_FILE"

BATCH_FILE="$(mktemp)"
cp "$QUEUE_FILE" "$BATCH_FILE"
: > "$QUEUE_FILE"

python3 - "$BATCH_FILE" "$RESULT_FILE" "$STATE_FILE" "$CONFIG_FILE" "$KEYS_DIR" "$SFTP_GROUP" <<'PY'
import json
import os
import pwd
import re
import subprocess
import sys
from datetime import datetime

batch_file, result_file, state_file, config_file, keys_dir, sftp_group = sys.argv[1:7]


def run(command):
    proc = subprocess.run(command, capture_output=True, text=True)
    output = (proc.stdout or proc.stderr or "").strip()
    return proc.returncode, output


def user_exists(username):
    try:
        pwd.getpwnam(username)
        return True
    except KeyError:
        return False


def ensure_group(name):
    code, _ = run(["getent", "group", name])
    if code == 0:
        return
    code, output = run(["groupadd", "--system", name])
    if code != 0 and "already exists" not in output.lower():
        raise RuntimeError(output or f"cannot create group {name}")


def ensure_user(username):
    if user_exists(username):
        return
    code, output = run(["useradd", "--system", "--gid", sftp_group, "--home", "/", "--shell", "/usr/sbin/nologin", username])
    if code != 0 and "already exists" not in output.lower():
        raise RuntimeError(output or f"cannot create user {username}")


def lock_or_unlock_account(username, enabled):
    if enabled:
        run(["usermod", "--expiredate", "-1", username])
    else:
        run(["usermod", "--expiredate", "1", username])
        run(["passwd", "-l", username])


def ensure_chroot(username, chroot_directory):
    chroot = os.path.realpath(chroot_directory)
    upload_dir = os.path.join(chroot, "upload")
    os.makedirs(chroot, exist_ok=True)
    os.chmod(chroot, 0o755)
    run(["chown", "root:root", chroot])
    os.makedirs(upload_dir, exist_ok=True)
    run(["chown", f"{username}:{sftp_group}", upload_dir])
    os.chmod(upload_dir, 0o750)
    return chroot


def write_keys(username, keys):
    key_path = os.path.join(keys_dir, username)
    sanitized = [item.strip() for item in keys if isinstance(item, str) and item.strip()]
    with open(key_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(sanitized))
        if sanitized:
            handle.write("\n")
    os.chmod(key_path, 0o600)
    run(["chown", "root:root", key_path])
    return key_path


def apply_password_hash(username, password_hash, auth_type):
    if auth_type in {"password", "both"} and password_hash:
        code, output = run(["usermod", "-p", password_hash, username])
        if code != 0:
            raise RuntimeError(output or "cannot apply password hash")
    elif auth_type == "key":
        run(["passwd", "-l", username])


def reload_sshd():
    for service in ("ssh", "sshd"):
        code, _ = run(["systemctl", "reload", service])
        if code == 0:
            return


try:
    with open(state_file, "r", encoding="utf-8") as handle:
        state = json.load(handle)
        if not isinstance(state, dict):
            state = {}
except (FileNotFoundError, json.JSONDecodeError):
    state = {}

ensure_group(sftp_group)
os.makedirs(keys_dir, exist_ok=True)

results = []
with open(batch_file, "r", encoding="utf-8") as handle:
    for line in handle:
        line = line.strip()
        if not line:
            continue
        now = datetime.utcnow().isoformat() + "Z"
        result = {"timestamp": now, "status": "error", "message": "unknown", "action": "sync", "username": "", "account_id": None}
        try:
            payload = json.loads(line)
            username = str(payload.get("username") or "").strip()
            action = str(payload.get("action") or "sync").strip()
            account_id = int(payload.get("account_id") or 0)
            enabled = bool(payload.get("enabled"))
            auth_type = str(payload.get("auth_type") or "password").strip().lower()
            keys = payload.get("public_keys") if isinstance(payload.get("public_keys"), list) else []
            chroot = str(payload.get("chroot_directory") or f"/srv/apps/{username}").strip()
            password_hash = payload.get("password_hash")

            result.update({"action": action, "username": username, "account_id": account_id})

            if not re.fullmatch(r"[a-z][a-z0-9_]{2,31}", username):
                raise RuntimeError("invalid username")
            if auth_type not in {"password", "key", "both"}:
                auth_type = "password"

            ensure_user(username)
            lock_or_unlock_account(username, enabled)

            if enabled:
                chroot = ensure_chroot(username, chroot)
                key_file = write_keys(username, keys)
                apply_password_hash(username, password_hash, auth_type)
                state[username] = {
                    "enabled": True,
                    "chroot_directory": chroot,
                    "auth_type": auth_type,
                    "key_file": key_file,
                }
                result["status"] = "ok"
                result["message"] = f"enabled; auth={auth_type}; chroot={chroot}"
            else:
                state[username] = {
                    "enabled": False,
                    "chroot_directory": chroot,
                    "auth_type": auth_type,
                    "key_file": os.path.join(keys_dir, username),
                }
                result["status"] = "disabled"
                result["message"] = "account disabled"
        except Exception as exc:  # pylint: disable=broad-except
            result["message"] = str(exc)
        results.append(result)

with open(state_file, "w", encoding="utf-8") as handle:
    json.dump(state, handle, ensure_ascii=True, indent=2)

lines = ["# Managed by myh-sftp-provision-worker.sh", ""]
for username in sorted(state.keys()):
    item = state[username]
    if not isinstance(item, dict):
        continue
    enabled = bool(item.get("enabled"))
    chroot = str(item.get("chroot_directory") or f"/srv/apps/{username}")
    auth_type = str(item.get("auth_type") or "password")
    key_file = str(item.get("key_file") or os.path.join(keys_dir, username))
    lines.append(f"Match User {username}")
    if enabled:
        lines.append(f"    ChrootDirectory {chroot}")
        lines.append("    ForceCommand internal-sftp -d /upload")
        lines.append("    AllowTcpForwarding no")
        lines.append("    X11Forwarding no")
        lines.append("    PermitTunnel no")
        lines.append("    PermitTTY no")
        lines.append(f"    AuthorizedKeysFile {key_file}")
        lines.append(f"    PasswordAuthentication {'yes' if auth_type in {'password', 'both'} else 'no'}")
        lines.append(f"    PubkeyAuthentication {'yes' if auth_type in {'key', 'both'} else 'no'}")
    else:
        lines.append("    ForceCommand /usr/sbin/nologin")
        lines.append("    PasswordAuthentication no")
        lines.append("    PubkeyAuthentication no")
    lines.append("")

with open(config_file, "w", encoding="utf-8") as handle:
    handle.write("\n".join(lines).rstrip() + "\n")

reload_sshd()

with open(result_file, "a", encoding="utf-8") as handle:
    for item in results:
        handle.write(json.dumps(item, ensure_ascii=True) + "\n")
PY

rm -f "$BATCH_FILE"
exit 0
