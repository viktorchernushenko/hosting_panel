# Server Architecture

## Current State

- Hostname: `my-home-server`
- User: `myserver`
- OS: Ubuntu 26.04 LTS
- Kernel: Linux 7.0.0-29-generic
- LAN IP: `192.168.1.100`
- Public IP observed from the host: `195.3.128.177`
- Primary interface: `wlp3s0`
- Resource envelope: 2 CPU, about 3.4 GiB RAM, about 458 GB disk

## Live Topology

- Nginx Proxy Manager is running in Docker and currently bound to loopback on ports 80, 81, and 443.
- Existing Docker compose projects were found in:
  - `/home/myserver/web-lab/docker-compose.yml`
  - `/home/myserver/nextcloud-docker/docker-compose.yml`
- No active compose inventory was found under `/srv` or `/opt`.

## Current Risk Flags

- `/dev/sda` reports pending and offline-uncorrectable sectors via SMART.
- `lab-db` is stuck in a restart loop because MariaDB crash recovery fails on `tc.log`.
- `nextcloud` is stuck in a restart loop because the data version is newer than the current image version.
- There is no shared proxy network normalization yet.

## Target Direction

- Keep Nginx Proxy Manager as the reverse proxy.
- Introduce a shared external proxy network for app stacks.
- Keep databases private on internal networks.
- Move toward `/srv/apps/<service-name>/` stacks with local templates.
- Do not break the current working panel or live Docker services during normalization.

## Phased Approach

1. Back up current configs and data references.
2. Recover or safely freeze the broken MariaDB / Nextcloud stacks.
3. Normalize shared Docker networks.
4. Add application registry and stack metadata to the existing panel.
5. Expand user/developer/admin views without duplicating functionality.
