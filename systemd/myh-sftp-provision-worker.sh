#!/usr/bin/env bash
set -euo pipefail

QUEUE_FILE="${HOSTING_PANEL_SFTP_PROVISION_QUEUE_FILE:-/home/myserver/hosting_panel/instance/sftp_provision_queue.jsonl}"
RESULT_FILE="${HOSTING_PANEL_SFTP_PROVISION_RESULT_FILE:-/home/myserver/hosting_panel/instance/sftp_provision_result.jsonl}"
STATE_FILE="${HOSTING_PANEL_SFTP_PROVISION_STATE_FILE:-/var/lib/myh-sftp-provision/state.json}"
LOCK_FILE="${HOSTING_PANEL_SFTP_PROVISION_LOCK_FILE:-/run/myh-sftp-provision/myh-sftp-provision.lock}"
CONFIG_FILE="${HOSTING_PANEL_SFTP_CONFIG_FILE:-/etc/ssh/sshd_config.d/99-myh-sftp.conf}"
KEYS_DIR="${HOSTING_PANEL_SFTP_KEYS_DIR:-/etc/ssh/myh-sftp-authorized-keys}"
SFTP_GROUP="${HOSTING_PANEL_SFTP_GROUP:-myh_sftp}"
PANEL_USER="${HOSTING_PANEL_SERVICE_USER:-myserver}"
PANEL_GROUP="${HOSTING_PANEL_SERVICE_GROUP:-myserver}"

umask 077
mkdir -p "$(dirname "$QUEUE_FILE")" "$(dirname "$RESULT_FILE")" "$(dirname "$STATE_FILE")" "$(dirname "$CONFIG_FILE")" "$KEYS_DIR"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  exit 0
fi

touch "$QUEUE_FILE" "$RESULT_FILE"
chown "$PANEL_USER:$PANEL_GROUP" "$QUEUE_FILE" "$RESULT_FILE"
chmod 600 "$QUEUE_FILE" "$RESULT_FILE"

if [[ ! -s "$QUEUE_FILE" ]]; then
  exit 0
fi

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
import time
from datetime import datetime, timezone

batch_file, result_file, state_file, config_file, keys_dir, sftp_group = sys.argv[1:7]


def run(command):
    proc = subprocess.run(command, capture_output=True, text=True)
    output = (proc.stdout or proc.stderr or "").strip()
    return proc.returncode, output


def run_host_mount(command):
    return run(["nsenter", "--mount=/proc/1/ns/mnt", "--", *command])


def is_lock_error(output):
    lowered = (output or "").lower()
    return (
        "cannot lock" in lowered
        or "не вдалося заблокувати" in lowered
        or "failure while writing changes to /etc/shadow" in lowered
        or "помилка під час спроби записати зміни до /etc/shadow" in lowered
    )


def run_with_retry(command, retries=30, delay=1.0):
    last_code = 0
    last_output = ""
    for attempt in range(retries):
        code, output = run(command)
        last_code, last_output = code, output
        if code == 0:
            return code, output
        if not is_lock_error(output) or attempt == retries - 1:
            return code, output
        time.sleep(delay * (attempt + 1))
    return last_code, last_output


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
    code, output = run_with_retry(["groupadd", "--system", name])
    if code != 0 and "already exists" not in output.lower():
        raise RuntimeError(output or f"cannot create group {name}")


def ensure_user(username):
    if user_exists(username):
        return
    code, output = run_with_retry(["useradd", "--system", "--gid", sftp_group, "--home", "/", "--shell", "/usr/sbin/nologin", username])
    if code != 0 and "already exists" not in output.lower():
        raise RuntimeError(output or f"cannot create user {username}")


def lock_or_unlock_account(username, enabled):
    if enabled:
        code, output = run_with_retry(["usermod", "--expiredate", "-1", username])
        if code != 0:
            raise RuntimeError(output or "cannot unlock account")
    else:
        code, output = run_with_retry(["usermod", "--expiredate", "1", username])
        if code != 0:
            raise RuntimeError(output or "cannot expire account")
        code, output = run_with_retry(["passwd", "-l", username])
        if code != 0:
            raise RuntimeError(output or "cannot lock account password")


def ensure_chroot(username, chroot_directory):
    chroot = os.path.realpath(chroot_directory)
    upload_dir = os.path.join(chroot, "upload")
    os.makedirs(chroot, exist_ok=True)
    os.chmod(chroot, 0o755)
    run(["chown", "root:root", chroot])
    os.makedirs(upload_dir, exist_ok=True)
    run(["chown", f"{username}:www-data", upload_dir])
    # setgid keeps uploads in the web-reader group; other hosting accounts
    # receive no permissions even though they share the SFTP account group.
    os.chmod(upload_dir, 0o2750)
    return chroot


def ensure_application_mounts(username, chroot, application_roots, previous_mounts):
    sites_root = os.path.join(chroot, "upload", "sites")
    os.makedirs(sites_root, exist_ok=True)
    run(["chown", "root:root", sites_root])
    os.chmod(sites_root, 0o755)
    desired = {}
    approved_root = "/home/myserver/hosting_panel/user_sites/"
    for item in application_roots:
        if not isinstance(item, dict):
            continue
        folder = str(item.get("folder_name") or "").strip()
        source = os.path.realpath(str(item.get("file_root") or ""))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", folder):
            raise RuntimeError("invalid application folder")
        legacy_upload = os.path.realpath(os.path.join(chroot, "upload"))
        mount_code, mount_root = run_host_mount(["findmnt", "-n", "-o", "FSROOT", "--target", legacy_upload])
        if mount_code == 0 and mount_root.startswith(approved_root):
            legacy_upload = os.path.realpath(mount_root)
        if (source == legacy_upload or source.startswith(legacy_upload + os.sep)
                or legacy_upload.startswith(source + os.sep)):
            # Legacy accounts already store the application directly inside
            # their chroot. Binding it below itself would recurse.
            continue
        if not source.startswith(approved_root) or not os.path.isdir(source):
            raise RuntimeError("application root is outside approved storage")
        destination = os.path.join(sites_root, folder)
        os.makedirs(destination, exist_ok=True)
        mounted, _ = run_host_mount(["mountpoint", "-q", destination])
        if mounted == 0:
            code, output = run_host_mount(["findmnt", "-n", "-o", "SOURCE", "--target", destination])
            if code == 0 and os.path.realpath(output) != source:
                code, unmount_output = run_host_mount(["umount", destination])
                if code != 0:
                    raise RuntimeError(unmount_output or "cannot replace application mount")
                mounted = 1
        if mounted != 0:
            code, mount_output = run_host_mount(["mount", "--bind", source, destination])
            if code != 0:
                raise RuntimeError(mount_output or "cannot bind application root")
        # The panel remains the filesystem owner. ACL grants only this SFTP
        # account access and is inherited by newly uploaded content.
        # Cover files generated by containers (not only future SFTP uploads).
        for acl in (["setfacl", "-R", "-m", f"u:{username}:rwX", source], ["setfacl", "-m", f"d:u:{username}:rwx", source]):
            code, acl_output = run(acl)
            if code != 0:
                raise RuntimeError(acl_output or "cannot apply application ACL")
        desired[destination] = source

    for destination in previous_mounts or []:
        if destination in desired:
            continue
        if not os.path.realpath(destination).startswith(os.path.realpath(sites_root) + os.sep):
            continue
        code, _ = run_host_mount(["mountpoint", "-q", destination])
        if code == 0:
            run_host_mount(["umount", destination])
    return sorted(desired.keys())


def write_keys(username, keys):
    key_path = os.path.join(keys_dir, username)
    sanitized = [item.strip() for item in keys if isinstance(item, str) and item.strip()]
    with open(key_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(sanitized))
        if sanitized:
            handle.write("\n")
    os.chmod(key_path, 0o640)
    run(["chown", f"root:{sftp_group}", key_path])
    return key_path


def apply_password_hash(username, password_hash, auth_type):
    if auth_type in {"password", "both"} and password_hash:
        code, output = run_with_retry(["usermod", "-p", password_hash, username])
        if code != 0:
            raise RuntimeError(output or "cannot apply password hash")
    elif auth_type == "key":
        code, output = run_with_retry(["passwd", "-l", username])
        if code != 0:
            raise RuntimeError(output or "cannot lock password for key-only auth")


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
os.chmod(keys_dir, 0o750)
run(["chown", f"root:{sftp_group}", keys_dir])

results = []
with open(batch_file, "r", encoding="utf-8") as handle:
    for line in handle:
        line = line.strip()
        if not line:
            continue
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
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
            application_roots = payload.get("application_roots") if isinstance(payload.get("application_roots"), list) else []

            result.update({"action": action, "username": username, "account_id": account_id})

            if not re.fullmatch(r"[a-z][a-z0-9_]{2,31}", username):
                raise RuntimeError("invalid username")
            if auth_type not in {"password", "key", "both"}:
                auth_type = "password"

            ensure_user(username)
            lock_or_unlock_account(username, enabled)

            if enabled:
                chroot = ensure_chroot(username, chroot)
                previous_mounts = (state.get(username) or {}).get("application_mounts", [])
                application_mounts = ensure_application_mounts(username, chroot, application_roots, previous_mounts)
                key_file = write_keys(username, keys)
                apply_password_hash(username, password_hash, auth_type)
                state[username] = {
                    "enabled": True,
                    "chroot_directory": chroot,
                    "auth_type": auth_type,
                    "key_file": key_file,
                    "application_mounts": application_mounts,
                }
                result["status"] = "ok"
                result["message"] = f"enabled; auth={auth_type}; chroot={chroot}"
            else:
                previous_mounts = (state.get(username) or {}).get("application_mounts", [])
                for destination in previous_mounts:
                    code, _ = run_host_mount(["mountpoint", "-q", destination])
                    if code == 0:
                        run_host_mount(["umount", destination])
                state[username] = {
                    "enabled": False,
                    "chroot_directory": chroot,
                    "auth_type": auth_type,
                    "key_file": os.path.join(keys_dir, username),
                    "application_mounts": [],
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

# The unprivileged panel must be able to append future jobs and consume
# provisioning results without being granted access to privileged state.
chown "$PANEL_USER:$PANEL_GROUP" "$QUEUE_FILE" "$RESULT_FILE"
chmod 600 "$QUEUE_FILE" "$RESULT_FILE"

rm -f "$BATCH_FILE"
exit 0
