# Changelog

MyH follows Semantic Versioning. Entries below are reconstructed from the actual Git history, code, configuration, UI and recorded tests; they do not create synthetic historical commits or tags.

## [2.2.0] - 2026-08-12

### Added

- Exposed the existing local MyH AI provider through a user assistant page and an administrator status page with provider, model, masked credential state and recorded usage.
- Added a read-only Create Site helper that recommends one of the verified runtimes and asks the local model for a short explanation.
- Added contextual AI access for sanitized site logs. AI has no shell, Docker socket, database, SSH or mutation tools.

### Changed

- Established `VERSION` as the canonical application version source used by the backend, public status API, UI and asset cache keys.
- Renamed ambiguous navigation entries to Account settings, Site settings and Platform modules.

### Fixed

- Removed the legacy dashboard form and POST path that silently defaulted new sites to Static. All creation now uses the backend-driven runtime wizard.

### Security

- Kept AI tenant authorization, CSRF checks, prompt length limits, log sanitization, provider isolation and audit records on all new AI workflows.

### Known limitations

- Off-server backup storage is not configured; verified local backups are not disaster-recovery copies.
- Telegram and SMTP delivery remain unavailable until server-side credentials are configured.
- Cloudflare manages browser-facing certificate renewal; local Certbot is not part of this ingress.
- AI inference uses a small CPU model and can take tens of seconds. Its output is advisory.
- Customer Docker Compose remains disabled; only policy-validated Dockerfile deployments are supported.
- WordPress has provisioning code but was not included in the current five-runtime lifecycle matrix, so it is not claimed as verified in this release.

## [2.1.0] - 2026-08-12

### Added

- Added independently reported local and encrypted off-server backup stages, checksum verification and disposable SQLite restore testing.
- Added Telegram/SMTP provider abstraction, alert deduplication and recovery events.
- Added live SFTP inventory, Cloudflare edge TLS expiry reporting and SQLite integrity/backup posture.

### Known limitations

- Off-server storage and external notification credentials were not configured on the production host.

## [2.0.0] - 2026-08-12

### Added

- Introduced a site-centred workspace linking overview, deployments, files, domain/SSL, database, logs, backups and site settings.
- Added responsive site tables, clearer resource workflows and shared destructive-action confirmation.

### Changed

- Moved infrastructure detail toward administrator views and simplified regular-user dashboard summaries.

### Fixed

- Restored the public homepage and corrected localized public/private navigation.
- Aligned UI claims with backend-confirmed capabilities and removed dead or misleading controls.

### Security

- Enforced signed GitHub webhooks with HMAC-SHA256 and constant-time comparison.
- Protected the notification webhook by default and retained explicit machine-to-machine exceptions only.

## [1.9.0] - 2026-08-10

### Added

- Added dedicated Sites, Domains, Databases, Backups, Logs and Profile routes.
- Added a health/attention dashboard, collapsible navigation and CGNAT-aware SFTP guidance.

## [1.8.0] - 2026-08-10

### Changed

- Introduced the shared responsive AppShell, grouped sidebar, topbar, design tokens and consistent cards, buttons, tables, forms and dialogs.
- Improved mobile navigation, focus handling, copy feedback and responsive content layout.

## [1.7.0] - 2026-08-10

### Added

- Added managed Static, PHP, Node.js, Python and Docker runtime architecture, application stacks, environment handling, resource limits and runtime controls.
- Added runtime-aware site creation, stack detection, Git deployment, deployment history, domain/SSL workflows and per-application database resources.
- Added file operations, site backups and SFTP account workflows with tenant-aware authorization.

### Changed

- Retained SQLite as the intentional internal metadata database while separating customer MySQL resources.

### Known limitations

- Later lifecycle verification was required before runtimes could be labelled `AVAILABLE`; that verification is recorded in 2.0.0-era production work and current `RUNTIMES.md`.

## [1.6.0] - 2026-08-07

### Added

- Added isolated OpenSSH `internal-sftp` provisioning and a privileged worker boundary.
- Added public health checks and expanded security regression coverage.

### Security

- Enforced CSRF on mutating user/API requests.
- Hardened ZIP extraction against traversal, symlinks, special files, archive bombs and oversized extraction.
- Reduced credential leakage and user-enumeration behavior.

## [1.5.0] - 2026-08-06

### Added

- Added a generic server installer, deployment wrapper, developer-managed homepage visibility and GitHub CI baseline.

## [1.0.0] - 2026-08-06

### Added

- Established the Flask hosting panel baseline with authentication, hosting users, sites, files, administration and initial deployment/operations controls.
- Added the first documented GitHub release and repository workflow.
