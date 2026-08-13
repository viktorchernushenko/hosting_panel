# MyH production security

Managed WordPress uses digest-pinned official runtime/CLI images, fixed-argv WP-CLI calls, stdin delivery for the initial administrator password, isolated database credentials and private networking. Nginx denies hidden files, `wp-config.php`, directory listing and uploaded PHP execution; dashboard file editing is disabled. See [WORDPRESS.md](WORDPRESS.md).

Manual ZIP extraction requires explicit confirmation and rejects traversal, links/devices, expansion abuse and existing-file collisions. Assisted configuration preserves an existing `wp-config.php` unless replacement is explicitly confirmed, in which case a timestamped backup is created first. Credential display is tenant-authorized, POST-only and marked `no-store`.

## Network and service boundary

- Public web traffic enters through Cloudflare Tunnel; Gunicorn and customer HTTP runtimes bind only to loopback.
- MySQL 8.4 binds only to the private `myh-db0` Docker bridge. Docker has no public TCP daemon and customer containers receive no Docker socket.
- UFW is active. Samba and CUPS are retained for an active dependency and restricted to the trusted LAN.
- SSH denies root login and Fail2ban is active. Administrator password SSH remains enabled until a tested administrator key prevents lockout.
- SFTP accounts use OpenSSH `internal-sftp`, per-user chroots, no shell and no forwarding.

## Application controls

- Secure, HTTP-only, SameSite session cookies and short session lifetime.
- CSRF validation on state-changing form and API requests, with a narrow allowlist for authenticated machine webhooks.
- Role permissions plus per-application tenant authorization on sites, files, databases, backups and logs.
- GitHub webhook HMAC-SHA256 verification using `X-Hub-Signature-256` and constant-time comparison.
- Notification webhook denied by default unless a server-side secret or explicit insecure-development override is configured.
- ZIP extraction rejects traversal, absolute paths, symlinks, special files, excessive entry count, expanded size and compression ratios.
- Runtime commands are constrained to application containers. Dockerfile deployments reject privileged mode, host namespaces, devices, public port control and host mounts; managed containers have resource/PID limits, dropped capabilities and bounded logs.
- Secrets remain in root-controlled files and are masked in UI/logs.

## Operational requirements

- Keep `/etc/hosting-panel.env`, MySQL provisioner configuration and private SSH/GPG material outside Git with restrictive permissions.
- Review failed services, unhealthy/restarting/OOM-killed containers, disk/RAM/swap, TLS expiry, backup checksums and restore tests.
- Off-server backup and external notification credentials are configuration requirements, not currently completed controls.
- Do not expose database, Docker or runtime ports to WAN.

## Planned hardening

- Administrator 2FA readiness.
- Key-only administrator SSH after a verified recovery path exists.
- Independently configured and restore-tested off-server backup storage.
## Production SSH boundary

Root SSH login is disabled. SFTP-only accounts are generated with `internal-sftp`, a root-owned chroot, no TTY, no tunnels, and explicit TCP and agent forwarding denial. The privileged worker validates usernames, chroot paths and key material before regenerating policy.

Global password authentication is not disabled automatically during unattended maintenance. It may be disabled only after a second concurrent administrator session proves key-based login, preventing an unrecoverable remote lockout.
