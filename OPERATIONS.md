# MyH operations

## Canonical production

- Repository and working directory: `/home/myserver/hosting_panel`.
- Service: `myh-guru.service`, Gunicorn on `127.0.0.1:5000` and `127.0.0.1:5001`.
- Ingress: Cloudflare → Cloudflare Tunnel → Gunicorn. Host nginx is not part of production ingress.
- Wildcard site traffic and `myh.guru` use the same panel proxy layer. `ssh.myh.guru` tunnels to OpenSSH port 22.
- MySQL listens on the private `hosting-databases` bridge only. Runtime HTTP ports and local AI bind to loopback. Docker has no TCP daemon listener.

## Health and incident checks

Check `/healthz`, `myh-guru`, `cloudflared`, `mysql`, `docker`, failed systemd units, unhealthy/restarting/OOM-killed containers, disk space/inodes and the latest backup result. A restart is a recovery action, not a root-cause diagnosis; inspect the unit and tunnel journals first.

## Ports

| Port | Binding | Purpose | Policy |
|---|---|---|---|
| 22 | public | SSH/SFTP and tunnel target | deliberate; Fail2ban and per-user SFTP policy |
| 139/445 | host, firewall LAN-only | Samba | retained for current LAN dependency |
| 631 | host, firewall LAN-only | CUPS | retained for current LAN dependency |
| 3306 | `172.23.0.1` | customer MySQL | Docker bridge only |
| 5000/5001 | loopback | MyH Gunicorn | never public directly |
| 8080 | loopback | Nextcloud | tunnel/proxy only |
| 11435 | loopback | local AI | panel API-key client only |
| 20000–29999 | loopback | customer runtimes | allocated dynamically, never public directly |

## Backups

`/mnt/myh-backup` has no backing device or network-mount configuration. The daily job now creates and verifies a local backup independently, then records remote status as `not_configured`; it never reports a local copy as off-server success. Configure `/etc/myh-backup.conf`, a GPG recipient and independent storage before claiming disaster recovery.

Database backups are logical `mysqldump` files with SHA-256 sidecars verified before restore. Run restore drills only against disposable resources or with explicit confirmation.

External alerts use Telegram/SMTP provider abstractions. Until server-side credentials are installed, Admin → Notifications reports Not configured and the watchdog records problems locally with deduplication state.

## Docker logging

The Docker default remains `json-file`; changing the daemon default would require a controlled Docker restart and recreation of existing containers. Managed MyH runtime templates and Nextcloud explicitly use `max-size=10m,max-file=3`, so current production containers are bounded without that risky restart.
