# Backup and restore

Platform audit snapshots are stored under `/srv/backups/platform-audit/<timestamp>` as root-only files with SHA-256 manifests. Verify with `sha256sum -c SHA256SUMS` from inside a snapshot. MySQL logical backups are under `/srv/backups/mysql`; site archives are managed per application by the panel.

Restore into a staging location first. Stop only the affected service, verify the archive and available disk space, restore configuration/data, validate ownership and secrets permissions, then start and health-check the service. For MySQL use the socket-authenticated root account and import the selected SQL dump. For Docker volumes, create the target volume and extract its archive into a temporary helper container. Never overwrite a live volume before preserving its current state.
