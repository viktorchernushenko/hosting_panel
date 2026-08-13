# MySQL hosting

MySQL 8.4 is enabled as `mysql.service`, bound to the private Docker bridge at `172.23.0.1:3306`. Root uses local socket authentication. The panel reads its restricted provisioner credentials from `/etc/mysql/myh-provisioner.cnf` (mode 0600) and invokes narrowly scoped stored procedures.

Customer backups use logical `mysqldump`, a temporary mode-0600 client option file, and a SHA-256 sidecar. Restore fails closed when the checksum is absent or invalid.

Every application receives a unique database and least-privilege database account. Containers connect through `hosting-databases`; bridge ICC is disabled to prevent lateral container traffic. Logical backups are written below `/srv/backups/mysql/<owner>/<resource>` and restore operations are audited by the panel.

The customer UI discovers MySQL health and version from the backend. PostgreSQL is shown as unavailable because no provisioner or private listener exists on this host; it must not be advertised as working until create, connect, tenant-isolation, backup, restore, reset and delete pass end to end.

## Advanced services

Supabase is a backend-as-a-service, not a third SQL engine. It is deferred on the current 2 CPU / 3.4 GiB RAM host because the existing panel, customer runtimes, Docker services and local AI already share constrained memory. Core MyH does not depend on Supabase. Any future installation must use the official self-hosted Docker architecture after a fresh capacity review.

## MyH application database

Panel metadata uses a separate SQLite DB at `instance/hosting.db`; it is not a customer database. Production currently has one backend instance, a small DB, low write concurrency, `journal_mode=DELETE`, 30-second busy timeout and a passing integrity check. SQLite is safe to retain and is not an error.

Backups use the SQLite Backup API, not a live-file copy. The disposable copy receives `PRAGMA integrity_check` and key-table count verification. The source is mode `0600` and not publicly served.

Migration becomes a separate project if MyH needs multiple writable replicas, HA/failover, sustained lock contention, materially higher concurrent writes, or transaction/size requirements SQLite cannot satisfy. PostgreSQL, MySQL and MariaDB must then be evaluated independently; customer MySQL does not predetermine this choice.
