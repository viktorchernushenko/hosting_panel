# Changelog

## v1.6.0 - 2026-08-07

- Security and architecture hardening across auth, CSRF, archive handling, SFTP provisioning path, and operational worker boundaries.
- Added strict CSRF enforcement for mutating `/api/*` routes with explicit allowlist for trusted machine-to-machine endpoints.
- Added minimal public health endpoint `GET /api/health` returning `{\"status\": \"ok\"}`.
- Hardened archive extraction (`safe_extract_zip`) with validation against path traversal, absolute paths, special file types, entry count limits, expanded size limits, and compression ratio abuse.
- Changed banned-account login behavior to generic response to avoid user enumeration side-channel.
- Removed bootstrap admin password echo in logs to reduce credential leakage risk.
- Implemented real OpenSSH SFTP provisioning integration:
	- Added queueing/hooks from panel account lifecycle.
	- Added root-side provisioning worker script with safer runtime locking.
	- Added systemd oneshot unit with writable runtime/state directories.
	- Added installer integration for worker, unit, and sudoers policy.
- Fixed installer sudoers generation issue related to unsupported `requiretty` behavior on Ubuntu/Debian targets.
- Added WP subpath permission gates for developer file operations.
- Added quota controls and checks in upload/save/restore/backup/job execution paths.
- Unified header/layout behavior across routes:
	- Single shared header structure.
	- Single compact UA/EN language switch in the geometric center.
	- Header logo served from `/static/favicon.ico`.
- Added/updated automated tests:
	- Existing deployment-history suite remains green.
	- New phase-2 security suite added:
		- CSRF rejection coverage for mutating API calls without token.
		- RBAC denial coverage for developer access to unassigned site resources.
		- Archive safety rejection coverage for malicious zip fixtures.
		- Health endpoint contract check.
		- Header language-switch regression check.
- SQLAlchemy 2.x compatibility cleanup:
	- Replaced legacy `Query.get()` usage with `Session.get()` in session user lookups.

## v1.5.0 - 2026-08-06

- Added universal installer script for any server.
- Added install guide for generic server deployment.
- Added compatibility deploy wrapper script.
- Added developer-managed homepage visibility controls.
- Added GitHub-ready README and CI workflow.

