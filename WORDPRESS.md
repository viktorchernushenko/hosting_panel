# Managed WordPress

WordPress is a managed application template, not a new general-purpose runtime. A WordPress site is stored as `runtime_type=php`, PHP 8.3 and `application_type=wordpress`; this keeps runtime detection deterministic while enabling WordPress-specific lifecycle actions.

## Installation

Create Site offers three practical paths:

- **One-click** provisions an isolated MySQL database and restricted database user, starts WordPress, then invokes WP-CLI with fixed argument arrays. The administrator password is supplied through stdin and is not written to MyH metadata or audit logs.
- **Assisted manual** starts an empty PHP environment, lets the user upload WordPress and create/select a tenant database, then generates a confirmed `wp-config.php`; the user completes `/wp-admin/install.php`.
- **Full manual** starts the same empty PHP environment but leaves configuration and installation to `/wp-admin/setup-config.php` and `/wp-admin/install.php`.

Manual ZIP extraction is explicit and protected against traversal, special entries, expansion abuse and existing-file overwrite. The workspace detects canonical WordPress directories without AI and shows a checklist, `/public_html`, File Manager, SFTP, database actions and connection details. Existing `wp-config.php` is never overwritten silently.

The stack uses the official WordPress 6 PHP 8.3 FPM Alpine and WordPress CLI 2.12.0 PHP 8.3 images pinned by digest. Nginx is loopback-only behind the platform proxy. MySQL is reachable only on the private `hosting-databases` Docker network.

## Operations

The site overview links to the public site, WordPress Admin, files, logs and Database Studio. WordPress version is read from installed core. WP-CLI uses an explicit command allowlist and argv execution; shell command construction is not used.

Plugin, theme and media files are available through the existing scoped file manager and SFTP permissions. The lifecycle smoke test additionally proves plugin install/activate/deactivate/delete, theme install/activate, media import, post creation and core version inspection through WP-CLI.

## Backup, restore and deletion

A managed WordPress backup consists of the normal ZIP file archive plus a MySQL logical dump and SHA-256 sidecar. Dump files are mode `0600`. Restore validates the dump checksum before clearing site files and restores files and database together. Backup deletion and retention remove all sidecars. Site deletion deprovisions the dedicated database/user and removes runtime, secrets and tenant storage.

## Security defaults

- `DISALLOW_FILE_EDIT` blocks dashboard plugin/theme editing.
- Nginx denies hidden files, direct `wp-config.php` access, directory listing and PHP execution below `wp-content/uploads`.
- PHP limits are explicit: 256 MB memory, 64 MB upload/post size and 120 seconds execution time.
- Runtime and CLI containers drop Linux capabilities and enable `no-new-privileges`.
- Credentials remain in mode-0600 application secret/environment storage and are never exposed in the create response.

Run the disposable automatic and manual verification with production environment variables loaded:

```bash
PYTHONPATH=. python scripts/wordpress_smoke.py
PYTHONPATH=. python scripts/wordpress_manual_smoke.py
```
