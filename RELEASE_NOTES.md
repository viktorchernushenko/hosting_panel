# MyH 2.5.0 release notes

Released: 2026-08-13

MyH 2.5.0 introduces the native Database Studio. Users can open an assigned MySQL database from the database list or site workspace and manage tables, data, SQL, imports, exports, backups and connection settings without leaving MyH or using a server-global database login.

Every Studio request is authenticated and checked against resource ownership. Database operations use the database's restricted application credential. SQL is single-statement and bounded by execution, row and response limits; server-level operations remain blocked by application policy and MySQL grants.

The production E2E created two disposable tenants and verified provisioning, table and row operations, logical backup/checksum/restore, password reset and cross-tenant denial before deleting all disposable resources. PostgreSQL remains honestly unavailable pending its own private provisioner and complete lifecycle.

See [DATABASES.md](DATABASES.md), [CHANGELOG.md](CHANGELOG.md) and [OPERATIONS.md](OPERATIONS.md).
