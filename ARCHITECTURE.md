# MyH production architecture

Canonical flow: **Internet → Cloudflare edge → Cloudflare Tunnel → Gunicorn `127.0.0.1:5000` → MyH/site proxy → loopback customer runtime**. Host nginx is inactive and is not a second ingress. The canonical repository and service working directory are `/home/myserver/hosting_panel`; no `/opt/hosting-panel` deployment is active.

The panel controls per-site Docker Compose projects whose HTTP ports bind only to loopback. Each project has a private bridge; database-capable runtimes also join the external `hosting-databases` network (`172.23.0.0/16`, bridge `myh-db0`, ICC disabled).

MySQL 8.4 runs on the host and listens only on `172.23.0.1:3306`. The panel uses a restricted provisioner account and creates one database account per application. Nextcloud is an independent healthy stack on `127.0.0.1:8080`. SFTP users are chrooted below `/srv/apps/<user>`.

Persistent state: panel metadata in `instance/`, site data in `/srv/apps`, MySQL in `/var/lib/mysql`, Nextcloud in its named volume, site backups in `instance/site_backups`, database backups in `/srv/backups/mysql`, and platform audit backups in `/srv/backups/platform-audit`.
