# MyH 2.10.0 release notes

MyH 2.10.0 closes the remaining managed WordPress lifecycle gaps. Full manual and assisted browser installers now transition from `needs_setup` to a working WordPress front controller without regenerating Nginx configuration. The official WordPress ZIP fits within guarded archive limits.

Managed WordPress cron runs per site through the existing Job Queue every 15 minutes with a 90-second timeout. It invokes WP-CLI inside the site's isolated runtime and never calls the public Flask proxy.

Restore permission reconciliation now happens after safe extraction. Production disposable E2E verified files and SQL sidecars, checksums, post/media deletion, destructive restore, and working HTTP afterward. Media, plugin, theme, permalink and core-update mechanisms were also exercised in disposable isolated environments.

Administrators receive a read-only orphan-directory inventory. No legacy directory is attached or deleted automatically.
