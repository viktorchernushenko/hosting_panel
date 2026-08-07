# Deployment

## Current Deployment

- Production service: `myh-guru.service`
- User: `myserver`
- Working directory: `/home/myserver/hosting_panel`
- Gunicorn binds to `127.0.0.1:5000` and `127.0.0.1:5001`
- Environment file: `/etc/hosting-panel.env`

## Safe Operational Flow

1. Inspect the current state.
2. Back up the relevant compose files, service files, and panel source.
3. Make the smallest change needed.
4. Run syntax / validation checks.
5. Re-check service health.
6. Document the change.

## What Not to Do

- Do not prune Docker resources blindly.
- Do not rewrite production compose stacks without backup.
- Do not expose internal databases to WAN.
- Do not change firewall rules without confirming the current NPM / router / WAN setup.

## Useful Checks

- `systemctl status myh-guru`
- `journalctl -u myh-guru -n 40 --no-pager`
- `docker ps`
- `docker compose ls`
- `docker stats --no-stream`

## Next Normalization Targets

- Shared proxy network
- Per-stack internal networks
- `/srv/apps/<stack>/` layout
- Stack templates for WordPress, Node, Python, PHP, and static sites
