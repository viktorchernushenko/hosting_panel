# MySQL hosting

Every managed WordPress application receives its own MySQL database and restricted database user. Credentials use application secret storage, connectivity is limited to the private `hosting-databases` network, and the site overview links to tenant-scoped Database Studio.

MySQL 8.4 is enabled as `mysql.service`, bound to the private Docker bridge at `172.23.0.1:3306`. Root uses local socket authentication. The panel reads its restricted provisioner credentials from `/etc/mysql/myh-provisioner.cnf` (mode 0600) and invokes narrowly scoped stored procedures.

Customer backups use logical `mysqldump`, a temporary mode-0600 client option file, and a SHA-256 sidecar. Restore fails closed when the checksum is absent or invalid.

Every application receives a unique database and least-privilege database account. Containers connect through `hosting-databases`; bridge ICC is disabled to prevent lateral container traffic. Logical backups are written below `/srv/backups/mysql/<owner>/<resource>` and restore operations are audited by the panel.

## Database Studio

The native Studio is reached through `/databases/<resource>/studio`. Browser requests pass through MyH authentication and resource ownership checks; the backend then connects with that resource's write-only tenant credential. It never uses the MySQL root or provisioner account for table, row or SQL operations.

Studio provides overview metadata, lazy table/structure/data APIs, pagination/search/sort, row and schema mutation APIs, a single-statement SQL editor, `.sql`/`.sql.gz` import, structure/data/full export, checksum backups, connection help and credential reset. SQL results are capped at 200 rows and 1 MiB, execution is limited to five seconds where MySQL supports `MAX_EXECUTION_TIME`, and server-level statements are blocked in addition to tenant grants.

PostgreSQL follows the same engine/service boundary but remains unavailable because no private listener, provisioner or full lifecycle implementation exists. Supabase, generated APIs, application auth, storage and realtime remain future services rather than dependencies of Studio.

The customer UI discovers MySQL health and version from the backend. PostgreSQL is shown as unavailable because no provisioner or private listener exists on this host; it must not be advertised as working until create, connect, tenant-isolation, backup, restore, reset and delete pass end to end.

## Advanced services

Supabase is a backend-as-a-service, not a third SQL engine. It is deferred on the current 2 CPU / 3.4 GiB RAM host because the panel, customer runtimes and Docker services share constrained memory. Core MyH does not depend on Supabase. Any future installation must use the official self-hosted Docker architecture after a fresh capacity review.

## MyH application database

Panel metadata uses a separate SQLite DB at `instance/hosting.db`; it is not a customer database. Production currently has one backend instance, a small DB, low write concurrency, `journal_mode=DELETE`, 30-second busy timeout and a passing integrity check. SQLite is safe to retain and is not an error.

Backups use the SQLite Backup API, not a live-file copy. The disposable copy receives `PRAGMA integrity_check` and key-table count verification. The source is mode `0600` and not publicly served.

Migration becomes a separate project if MyH needs multiple writable replicas, HA/failover, sustained lock contention, materially higher concurrent writes, or transaction/size requirements SQLite cannot satisfy. PostgreSQL, MySQL and MariaDB must then be evaluated independently; customer MySQL does not predetermine this choice.
