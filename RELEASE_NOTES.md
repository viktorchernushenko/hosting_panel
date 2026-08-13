# MyH 2.7.1 release notes

Released: 2026-08-13

MyH 2.7.1 is a production verification and SFTP hardening release. The privileged SFTP provisioner now explicitly disables SSH agent forwarding in addition to the existing chroot, internal-SFTP, no-TTY, no-tunnel and no-TCP-forwarding controls.

The live SSH policy passed `sshd -t`, was reloaded without interrupting the service, and reports both TCP and agent forwarding disabled for each enabled SFTP-only account. Global administrator authentication was deliberately left unchanged because a second key-authenticated session was not available to prove lockout safety.

Production E2E revalidated all five runtime lifecycles and the PHP-to-private-MySQL path, including disposable database provisioning, restricted credentials, logical backup, checksum, restore and cleanup.

See [SFTP.md](SFTP.md), [SECURITY.md](SECURITY.md), [RUNTIMES.md](RUNTIMES.md), [DATABASES.md](DATABASES.md), [CHANGELOG.md](CHANGELOG.md) and [OPERATIONS.md](OPERATIONS.md).
