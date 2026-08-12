# SFTP access

Provisioned users use OpenSSH `internal-sftp`, chroot to `/srv/apps/<user>`, start in `/upload`, and cannot obtain a shell, TTY, forwarding, or tunnels. Public keys are synchronized by `myh-sftp-provision-worker.service`.

Validate configuration with `sshd -t` and effective policy with `sshd -T -C user=<name>,host=localhost,addr=127.0.0.1`. A user must never see another user's filesystem through the chroot.

## Account review (2026-08-12)

| User | UID | Evidence | Classification | Safe to remove |
|---|---:|---|---|---|
| `developer` | 993 | Panel account and assigned paths | PRODUCTION | No |
| `demo` | 101 | Successful SFTP activity and active bind path | TEST_ACTIVE | No |
| `testuser1` | 995 | Same-day isolated SFTP E2E activity | TEST_ACTIVE | No |
| `testuser2` | 994 | Same-day isolated SFTP E2E activity | TEST_ACTIVE | No |
| `test56` | 992 | Own site directory without panel record | UNKNOWN | No |

No account is proven `ORPHAN_TEST`; none was deleted. Admin → SFTP displays the live inventory. Future removal requires a chroot/account-metadata archive, disable-first grace period, and explicit shared-path validation.
