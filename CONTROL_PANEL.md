# MyH control panel

## Production identity

- Release: `2.4.0`, sourced from [`VERSION`](VERSION).
- Repository and service working directory: `/home/myserver/hosting_panel`.
- Release branch: `platform/universal-hosting-upgrade`.
- Service: `myh-guru.service`, Gunicorn on loopback ports 5000/5001.
- Ingress: Cloudflare Tunnel; host nginx is not a second ingress.

## Roles and product areas

Authenticated users receive a shared responsive AppShell with Dashboard, Sites, Domains, Databases, File access, Deployments, Backups, Logs and Account settings. A site workspace connects overview, runtime/deployment, files, domain/SSL, databases, logs, backups and site settings.

Administrators additionally receive users, applications, infrastructure, containers, runtimes, backup, notifications, SFTP, SSL/domains, security/audit and platform modules. Authorization remains enforced in backend routes; hidden navigation is not the security boundary.

## Verified control paths

- Runtime-aware site creation and five-runtime lifecycle management.
- Tenant-scoped file, backup, log, database and SFTP workflows.
- Upload/ZIP and Git deployments with history and signed GitHub webhook support.
- Cloudflare DNS and edge TLS status.
- SQLite internal metadata health and isolated MySQL customer resources.
- Deterministic runtime detection and common log-error classification.

See [PRODUCT_AUDIT.md](PRODUCT_AUDIT.md) for the evidence map and explicit verification limits.
