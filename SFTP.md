# SFTP access

Provisioned users use OpenSSH `internal-sftp`, chroot to `/srv/apps/<user>`, start in `/upload`, and cannot obtain a shell, TTY, forwarding, or tunnels. Public keys are synchronized by `myh-sftp-provision-worker.service`.

Validate configuration with `sshd -t` and effective policy with `sshd -T -C user=<name>,host=localhost,addr=127.0.0.1`. A user must never see another user's filesystem through the chroot.
