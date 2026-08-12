# MyH production security

## Network and service boundary

- Public web traffic enters through Cloudflare Tunnel; Gunicorn, customer HTTP runtimes and local AI bind only to loopback.
- MySQL 8.4 binds only to the private `myh-db0` Docker bridge. Docker has no public TCP daemon and customer containers receive no Docker socket.
- UFW is active. Samba and CUPS are retained for an active dependency and restricted to the trusted LAN.
- SSH denies root login and Fail2ban is active. Administrator password SSH remains enabled until a tested administrator key prevents lockout.
- SFTP accounts use OpenSSH `internal-sftp`, per-user chroots, no shell and no forwarding.

## Application controls

- Secure, HTTP-only, SameSite session cookies and short session lifetime.
- CSRF validation on state-changing form and API requests, with a narrow allowlist for authenticated machine webhooks.
- Role permissions plus per-application tenant authorization on sites, files, databases, backups, logs and AI diagnostics.
- GitHub webhook HMAC-SHA256 verification using `X-Hub-Signature-256` and constant-time comparison.
- Notification webhook denied by default unless a server-side secret or explicit insecure-development override is configured.
- ZIP extraction rejects traversal, absolute paths, symlinks, special files, excessive entry count, expanded size and compression ratios.
- Runtime commands are constrained to application containers. Dockerfile deployments reject privileged mode, host namespaces, devices, public port control and host mounts; managed containers have resource/PID limits, dropped capabilities and bounded logs.
- Secrets remain in root-controlled files, are masked in UI/logs and are not returned through the AI status API/UI.

## AI boundary

MyH AI is read-only and loopback-only. The panel supplies sanitized, truncated context after authentication and tenant checks. Prompt/output sizes, request rates and daily usage are bounded and audited. The provider has no root shell, Docker socket, arbitrary SQL, SSH or mutation interface.

## Operational requirements

- Keep `/etc/hosting-panel.env`, MySQL provisioner configuration, AI key and private SSH/GPG material outside Git with restrictive permissions.
- Review failed services, unhealthy/restarting/OOM-killed containers, disk/RAM/swap, TLS expiry, backup checksums and restore tests.
- Off-server backup and external notification credentials are configuration requirements, not currently completed controls.
- Do not expose database, Docker, runtime or AI ports to WAN.

## Planned hardening

- Administrator 2FA readiness.
- Key-only administrator SSH after a verified recovery path exists.
- Independently configured and restore-tested off-server backup storage.
