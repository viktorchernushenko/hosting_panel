# MyH 2.3.0 release notes

MyH 2.3.0 completes the database and AppShell UX pass: localized modal-based database creation, live MySQL availability/version, an honest unavailable PostgreSQL state, clearer user navigation, and accessible mobile drawer behavior. See `CHANGELOG.md` for verified changes and known limitations.

## Previous release: 2.2.0

Released: 2026-08-12

## What's new

MyH now uses one runtime-aware Create Site workflow for Static, PHP, Node.js, Python and Docker. The former dashboard form that silently created Static sites has been removed. A visible MyH AI area provides read-only site diagnostics, sanitized log explanations and contextual runtime guidance.

Navigation now distinguishes Account settings, Site settings and Platform modules. Administrators also have an AI status page showing configuration and usage without exposing the provider key.

## Security

The release preserves backend authorization, tenant isolation and CSRF enforcement. AI receives only constrained project descriptions or server-built diagnostic context; logs are sanitized and truncated, prompts are bounded, usage is rate-limited and audited, and the provider has no mutation tools or privileged system access.

## Hosting and operations

The production registry reports Static, PHP 8.2, Node.js 22, Python 3.12 and validated Dockerfile hosting as `AVAILABLE` only after complete create/build/start/HTTP/health/log/restart/stop/delete/security verification. MyH continues to use SQLite for its internal metadata and MySQL 8.4 for isolated customer databases.

## Known limitations

- Off-server backup target: not configured.
- Telegram/SMTP credentials: not configured.
- Cloudflare manages browser-facing certificate renewal.
- AI uses a small CPU model, so requests can take tens of seconds and answers remain advisory.
- Customer Docker Compose is disabled; Dockerfile deployments are policy validated.
- WordPress was not part of the current five-runtime lifecycle verification.

See [CHANGELOG.md](CHANGELOG.md) for the reconstructed product history and [OPERATIONS.md](OPERATIONS.md) for production details.
