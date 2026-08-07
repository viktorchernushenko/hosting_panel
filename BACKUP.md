# Backup

## What Needs Backing Up Before Changes

- Panel source code
- Panel templates and static assets
- `instance/hosting.db`
- `/etc/hosting-panel.env`
- `web-lab/docker-compose.yml`
- `nextcloud-docker/docker-compose.yml`
- Docker compose environment files for live stacks
- Systemd unit overrides and service files

## Current Backup Notes

- The panel already creates site-level archives under its own instance backup structure.
- That is not a full disaster-recovery strategy for the host or Docker volumes.
- A centralized backup registry still needs to be designed.

## Important Caveat

- A copy on the same disk is not full disaster recovery.
- Before touching live database or Nextcloud state, confirm a recoverable backup path.

## Suggested Backup Layers

- Application config backups
- Database dumps
- Docker volume snapshots where safe
- User file archives
- Panel database and config backups

## Rollback Principle

- Keep the previous config intact until the new path is validated.
- Prefer additive changes over destructive replacements.
