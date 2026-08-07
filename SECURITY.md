# Security

## Current Posture

- SSH is LAN-only.
- UFW already allows SSH, Samba, and CUPS only from `192.168.1.0/24`.
- Nginx Proxy Manager is loopback-bound on the host.
- Panel sessions use secure cookie flags in code.
- CSRF protection is implemented for normal form submissions.

## Known Concerns

- MariaDB and Nextcloud are in restart loops.
- SMART warnings on `/dev/sda` need attention.
- RBAC is coarse and still needs permissions beyond admin / user.
- Docker access strategy still needs least-privilege review.
- Public proxy exposure should not be expanded until router / WAN / CGNAT are confirmed.

## Current Security Rules

- Do not expose database ports to WAN.
- Do not expose SSH to WAN directly.
- Do not add arbitrary shell execution APIs.
- Do not store secrets in frontend state or logs.
- Do not weaken file ownership or permissions broadly.

## Next Security Work

- Granular permissions
- Audit log expansion
- 2FA readiness for developer/admin users
- Safer Docker integration
- Shared proxy network review
- Backup restore confirmation flow
