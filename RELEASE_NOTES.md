# MyH 2.9.0 release notes

MyH 2.9.0 makes WordPress creation a generic, phase-aware provisioning pipeline for every tenant. Automatic sites receive an isolated PHP-FPM/Nginx stack, canonical `public_html`, an isolated MySQL resource, resumable official WordPress files, one-click installation and application-aware status. Manual sites receive the same isolated infrastructure without placeholder content and remain in `needs_setup` until files and installation are present.

Failed or incomplete WordPress stacks can be reconciled through **Повторити provisioning**. Retry reuses the site, port, stack and database resources. A dedicated FastCGI health endpoint verifies actual PHP execution without depending on WordPress canonical redirects.

Production validation covered three simultaneous UI-created sites owned by different users, distinct host content, cross-database denial, HTTPS, `/wp-admin/`, restart persistence, and a WordPress files-plus-database backup with checksum sidecars.

Request-driven WordPress cron is disabled in managed auto/assisted configurations to prevent loopback callbacks from exhausting the panel proxy. Workloads requiring scheduled WordPress events should use a managed external cron runner; that runner is not included in this release.
