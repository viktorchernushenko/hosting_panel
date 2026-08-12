# Server architecture

The canonical current topology is documented in [ARCHITECTURE.md](ARCHITECTURE.md) and the operational bindings in [OPERATIONS.md](OPERATIONS.md).

Production traffic flows through Cloudflare edge and Cloudflare Tunnel to loopback Gunicorn. Managed customer runtimes use isolated Docker Compose projects with loopback HTTP ports and resource limits. Customer MySQL is private to the `myh-db0` bridge. SFTP is chrooted below `/srv/apps`; local AI is loopback-only.

Persistent production state is intentionally outside container layers. Do not normalize or move live application, database, SFTP or backup paths without a verified backup and restore plan.
