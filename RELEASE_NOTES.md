# MyH 2.8.1 release notes

Released: 2026-08-13

MyH 2.8.1 completes the first-class manual WordPress workflow introduced by 2.8.0. Create Site now clearly distinguishes automatic installation from manual installation. Manual mode starts an empty, working PHP 8.3 environment without forcing WordPress files or a database.

The workspace presents a checklist for files, database, `wp-config.php` and the standard installer. Users can upload files or ZIP packages, use SFTP, explicitly extract archives, optionally flatten the official `wordpress/` root, provision a private database, view credentials on a tenant-authorized no-store page and use either full manual `setup-config.php` or assisted configuration.

Assisted configuration generates unique salts and private database settings but does not create the WordPress administrator or complete installation. Existing `wp-config.php` files are never overwritten silently; confirmed replacement creates a timestamped backup first.

Disposable E2E now proves automatic, full-manual and assisted-manual flows. Automatic lifecycle verification also covers pretty permalinks, core update checking and a real files-plus-database backup/modify/restore cycle.

See [WORDPRESS.md](WORDPRESS.md), [BACKUP_RESTORE.md](BACKUP_RESTORE.md), [SECURITY.md](SECURITY.md) and [CHANGELOG.md](CHANGELOG.md).
