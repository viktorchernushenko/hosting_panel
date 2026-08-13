# Changelog

## [2.9.2] - 2026-08-13

### Fixed

- Preserved raw upstream `Content-Encoding` while stripping hop-by-hop and stale length headers in the customer runtime proxy.
- Prevented the proxy HTTP client from following application redirects through public domains, restoring correct WordPress `/wp-admin/` redirects and browser rendering.
- Forwarded the canonical public port alongside host and scheme metadata.

## [2.9.1] - 2026-08-13

### Fixed

- Normalized MySQL `INFORMATION_SCHEMA` metadata casing at the Database Studio boundary, fixing HTTP 500 responses when opening WordPress table data.
- Added stable JSON responses for unexpected API errors and retryable Database Studio error/empty states.
- Added regression coverage for metadata normalization and MySQL value serialization.

## [2.9.0] - 2026-08-13

### Added

- Persisted WordPress provisioning phases and an idempotent provisioning retry action.
- Added a dedicated Nginx-to-PHP-FPM health endpoint for every managed WordPress stack.
- Added explicit site-local `503` responses for manual WordPress sites awaiting uploaded files.

### Changed

- WordPress and PHP sites now use canonical `public_html` roots without static placeholders.
- WordPress core population is resumable and guarded by a completion marker.
- Runtime environment serialization now safely preserves PHP dollar-prefixed variables.
- Customer proxy requests preserve the requested host, and managed WordPress disables request-driven cron callbacks that can block the panel proxy.

### Fixed

- Fixed partial WordPress core copies after first-start health timeouts.
- Fixed invalid `WORDPRESS_CONFIG_EXTRA` values caused by Compose interpolation.
- Fixed misleading root-URL health checks and inconsistent access roots after failed provisioning.

MyH follows Semantic Versioning. Entries below are reconstructed from the actual Git history, code, configuration, UI and recorded tests; they do not create synthetic historical commits or tags.

## [2.8.2] - 2026-08-13

### Fixed

- Made the `manage_site.html` WordPress state contract unconditional and added defensive template access for optional application context.
- Combined compose project ownership, container state, readiness and deterministic WordPress installation state for workspace status.
- Classified WordPress records without a deployment stack or files as `Needs setup` and disabled public/admin actions until ready.

## [2.8.1] - 2026-08-13

### Added

- Added first-class automatic and manual WordPress choices, with manual database provisioning optional at creation time.
- Added manual installation checklist and deterministic states for missing files, configuration required, installation required, database failure and installed WordPress.
- Added explicit safe ZIP extraction, optional single-root flattening, assisted `wp-config.php` generation and no-store database credential display.
- Added full-manual browser E2E through `setup-config.php` and assisted-manual browser E2E through `install.php` without automatic core installation.

### Changed

- WordPress backup/restore E2E now verifies pretty permalinks, core update availability, posts, media and plugin files after a real file/database rollback.
- WordPress file reconciliation uses tenant-scoped group permissions and never world-writable modes.

### Security

- ZIP extraction refuses traversal, special entries, expansion abuse and silent overwrites.
- Existing `wp-config.php` is preserved unless replacement is explicitly confirmed; replacement creates a timestamped backup.
- Database credentials are tenant-authorized, POST-only and served with `no-store` headers.

## [2.8.0] - 2026-08-13

### Added

- Added WordPress as a first-class Create Site choice implemented as a managed application template over PHP 8.3 and an isolated private MySQL database.
- Added one-click and standard browser installation paths, fixed-argv WP-CLI management, WordPress Admin access, version reporting and permalink setup.
- Added WordPress-aware backup and restore covering both files and a checksummed logical database dump.
- Added a disposable WordPress lifecycle smoke test covering installation, public/admin HTTP, posts, media, plugins, themes and database isolation.

### Security

- Pinned the official WordPress runtime and WP-CLI images by digest, passed the administrator password over stdin, and kept credentials out of audit output.
- Disabled dashboard file editing, directory listing, direct `wp-config.php` access and PHP execution under uploads; applied bounded PHP upload, memory and execution limits.
- Kept each WordPress database/user least-privilege and reachable only over the private database network.

## [2.7.1] - 2026-08-13

### Changed

- Re-ran the complete Static, PHP, Node.js, Python and custom Docker lifecycle smoke matrix against production infrastructure.
- Re-verified MySQL private networking, least-privilege provisioner grants, PHP/PDO connectivity and logical backup/checksum/restore.

### Security

- Explicitly disabled SSH agent forwarding for every enabled SFTP-only account in both the canonical privileged provisioner and live generated SSH policy.
- Validated the generated policy with `sshd -t`, reloaded only SSH and confirmed TCP and agent forwarding are disabled per SFTP account.

### Known limitations

- Global SSH password authentication and admin forwarding remain unchanged until an administrator confirms a second simultaneous key-authenticated SSH session.
- Off-server backup and notification providers still require external storage/provider configuration.
- Four system SFTP accounts remain `UNKNOWN`; no account or data was deleted.

## [2.7.0] - 2026-08-13

### Added

- Added in-panel Telegram Bot Token and Chat ID configuration with enable/disable and provider-specific delivery tests.
- Added standard SMTP host, port, authentication, sender, recipients and None/TLS/SSL configuration with provider-specific delivery tests.
- Added admin-only notification settings/configure/toggle/test endpoints and localized provider lifecycle states.

### Changed

- Notification settings are loaded dynamically, so configuration changes do not require a service restart.
- Operational notification delivery uses UI-managed settings when present and remains compatible with existing environment configuration.
- The dashboard considers a provider configured only after an enabled provider passes a real delivery test.

### Security

- Secrets are encrypted at rest with authenticated Fernet encryption using a key derived from the existing application master secret.
- Configuration writes are validated, locked, backed up, atomic and mode `0600`; unrelated provider settings are preserved.
- Bot Tokens and SMTP passwords are never returned by the API or rendered into HTML, and blank secret fields preserve existing values.
- Added CSRF, admin-only authorization, five-tests-per-ten-minutes rate limiting and secret-free audit events.

## [2.6.0] - 2026-08-13

### Added

- Added a canonical admin platform-status model and protected `/api/platform/status` endpoint with stable item fields and explicit `requires_action` semantics.
- Added a separate healthy Platform Status section to the administrative dashboard.

### Changed

- The Needs Attention section now renders only actionable findings instead of hardcoded informational rows.
- Cloudflare-managed healthy TLS and healthy SQLite integrity moved to Platform Status.
- Backup, notification and SFTP findings now reflect verified runtime state and link directly to the relevant admin workflow.

### Security and operations

- Confirmed that off-server backup remains unconfigured; local backup success is not represented as disaster recovery.
- Confirmed that Telegram and SMTP are not configured; the existing test action reports real provider results.
- Classified the panel-linked `developer` SFTP account as `PRODUCTION`; four unlinked accounts remain `UNKNOWN` and not safe to remove pending owner/dependency confirmation. No account or data was deleted.

## [2.5.0] - 2026-08-13

### Added

- Added native Database Studio with Overview, Tables, SQL, Import/Export, Backups, Connection and Settings areas.
- Added tenant-authorized APIs for table metadata, structure, paginated data, row CRUD, table creation and controlled schema changes.
- Added a bounded single-statement SQL editor with result/response limits, destructive confirmation, server-level statement blocking and privacy-preserving audit history.
- Added `.sql` and `.sql.gz` import plus structure-only, data-only and full logical exports without exposing passwords in process arguments.

### Changed

- Made Database Studio the primary database action in the global database list and site workspace.
- Reused existing checksum backup/restore and write-only credential reset workflows inside Studio.

### Security

- Studio connects with the individual application's database role, never MySQL root or the provisioning identity.
- Added resource ownership checks to every Studio route, identifier quoting/type allowlists, query limits and cross-tenant regression coverage.

### Known limitations

- PostgreSQL remains unavailable until a private provisioner and complete Studio lifecycle pass E2E.
- Query cancellation is deferred; bounded MySQL query execution and socket timeouts are enforced instead.
- Structured row/schema mutation APIs are present; the initial UI emphasizes browsing and SQL for advanced mutations.

## [2.4.0] - 2026-08-13

### Removed

- Removed MyH AI from user/admin navigation, Create Site, the site workspace and monitoring UI.
- Removed `/ai`, `/developer/ai`, `/api/ai/runtime-recommend` and `/api/sites/<id>/ai/diagnose`.
- Removed the provider implementation, prompt/rate/usage code, AI configuration templates and repository systemd units.
- Removed the production inference service, model/runtime files, key reference and AI-only service identity after dependency verification.

### Changed

- Replaced inference-assisted runtime choice with the existing deterministic project-marker detector.
- Added deterministic classification for common port, environment, dependency, permission, database and storage log failures.
- Preserved direct DNS, HTTPS and database diagnostics without introducing a replacement inference layer.

### Security

- Removed the model endpoint, provider credential, prompt-processing surface and AI-specific service access.
- Preserved historical `ai.*` events in the shared audit log; no AI-specific database tables existed.

### Known limitations

- Browser DevTools screenshots and network-console verification remain unavailable in this environment; route, template, server and external HTTP checks are used instead.

## [2.3.0] - 2026-08-13

### Added

- Added a focused database creation dialog with site, backend-reported engine availability, name and review guidance.
- Added regression coverage for Ukrainian database UI and rejection of unavailable engines before provisioning.

### Changed

- Reorganized regular-user navigation into Hosting, Operations, Tools and Account while retaining the shared role-aware AppShell.
- Made MySQL availability and displayed version come from a live restricted backend query; PostgreSQL is explicitly unavailable until its full lifecycle is provisionable.
- Reworked database rows for compact desktop tables and labelled mobile cards, with masked write-only passwords and working copy/reset/backup/restore/delete controls only.

### Fixed

- Localized the database workflow and core Ukrainian navigation labels instead of mixing English surrounding UI.
- Confined sidebar scrolling to navigation content and added mobile drawer focus containment, Escape close and focus return.
- Removed the stale hardcoded current version from the administrator release view.

### Security

- Rejects unsupported database engines server-side before any provisioner call; UI availability is not treated as an authorization boundary.

### Known limitations

- PostgreSQL is not installed/provisionable and remains unavailable.
- Supabase is deferred after the current host capacity review; core hosting remains independent of it.
- Browser DevTools screenshots and responsive visual inspection require a browser integration and remain separate from server-side verification.

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
- Ensured a site's scoped backup directory exists before creating its first archive, including on clean installations and CI runners.

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
