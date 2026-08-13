# MyH 2.8.0 release notes

Released: 2026-08-13

MyH 2.8.0 promotes WordPress to a native managed application in Create Site. It remains architecturally PHP 8.3 plus MySQL rather than a separate general-purpose runtime, while the panel records `application_type=wordpress` for lifecycle behavior.

Both one-click setup and the standard WordPress installer are supported. One-click setup provisions a dedicated database and restricted user, starts the official digest-pinned images, installs core through fixed-argv WP-CLI, configures HTTPS-aware URLs and pretty permalinks, and never persists the generated administrator password in panel metadata or audit logs.

The overview links directly to WordPress Admin and Database Studio and reports the detected WordPress version. Existing file tools cover plugin, theme and media content; WP-CLI lifecycle behavior is verified by the disposable production smoke test.

Backups now pair the site archive with a mode-0600 checksummed MySQL dump. Restore verifies the database sidecar before replacing site files, then restores both layers. Site deletion continues to remove the runtime, database resources, secrets and tenant files.

Security defaults deny direct `wp-config.php` access, hidden files, directory listings and PHP execution inside uploads; dashboard file editing is disabled and PHP resource limits are explicit.

See [WORDPRESS.md](WORDPRESS.md), [RUNTIMES.md](RUNTIMES.md), [DATABASES.md](DATABASES.md), [BACKUP_RESTORE.md](BACKUP_RESTORE.md), [SECURITY.md](SECURITY.md) and [CHANGELOG.md](CHANGELOG.md).
