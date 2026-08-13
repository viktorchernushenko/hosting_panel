# SFTP access

Provisioned users use OpenSSH `internal-sftp`, chroot to `/srv/apps/<user>`, start in `/upload`, and cannot obtain a shell, TTY, TCP/agent forwarding, or tunnels. Public keys are synchronized by `myh-sftp-provision-worker.service`.

Validate configuration with `sshd -t` and effective policy with `sshd -T -C user=<name>,host=localhost,addr=127.0.0.1`. A user must never see another user's filesystem through the chroot.

## Account review (2026-08-13)

| User | UID | Evidence | Classification | Safe to remove |
|---|---:|---|---|---|
| `developer` | 993 | Panel account and assigned paths | PRODUCTION | No |
| `demo` | 101 | No panel record; empty upload; no key or running process | UNKNOWN | No |
| `testuser1` | 995 | No panel record; empty upload; no key or running process | UNKNOWN | No |
| `testuser2` | 994 | No panel record; empty upload; no key or running process | UNKNOWN | No |
| `test56` | 992 | No panel record; empty upload; no key or running process | UNKNOWN | No |

The four unlinked accounts have test-like names, but names and inactivity are not sufficient proof of ownership or safe removal. No account is proven `ORPHAN_TEST`; none was deleted. Admin → SFTP displays the live inventory. Future removal requires owner confirmation, a chroot/account-metadata archive, disable-first grace period, and explicit shared-path validation.

The 2026-08-13 production policy verification returned `allowtcpforwarding=no` and `allowagentforwarding=no` for every enabled SFTP-only account. Filesystem checks confirmed an active account can write its own upload directory while cross-account read, write and delete are denied. Full network-login coverage for every legacy account remains dependent on its owner-held password/private key.
