# Private Direct archive transport

The source worker needs an independent verified archive read before exchanging
SQL payload bytes for WAV references. The existing archive broker listens on a
private Unix socket under the archive reader identity; no public object endpoint
or upload credential is used for this transport.

The new SSH connection forwards one source Unix socket to that existing archive
broker socket. It reuses the established restricted transport identity and pinned
host keys without restarting the existing source-link connection. Unix-socket
forwarding uses the documented OpenSSH `-R remote_socket:local_socket` form.
See the [OpenSSH manual](https://man.openbsd.org/ssh).

## Preparation and isolation

`deploy/archive-server/prepare-direct-archive-relay.py source` creates a dedicated
empty group, one new runtime directory and an inactive socket-permission service.
The directory is owned by the existing forwarding-only account, with mode 2710;
new sockets inherit the dedicated group. No ordinary user is added to that group.
Only compatible worker containers receive that supplementary group explicitly.

The bounded permission helper changes only a socket with the expected owner,
group and initial 0600 permissions to 0660. It refuses regular files, symlinks,
unexpected owners and public permissions. Its service has only `CAP_FOWNER`,
16 MiB memory, 1% CPU, two tasks and no IP networking.

`deploy/archive-server/prepare-direct-archive-relay.py archive` prepares a separate,
inactive SSH service limited to 24 MiB memory and 5% CPU within the existing
archive slice. Private keys stay on the archive host and are provided only through
systemd credentials. The source ingest services, listeners and existing SSH link
are untouched. Both preparations refuse different existing operator files.

Preparation does not start either service, change worker configuration, exchange
payload bytes, enable deletion or modify any original recording.

## Activation gates

1. Pin the preparation hashes, running image identities and current limits.
2. Verify the source SSH server accepts the restricted identity's Unix forwarding.
   If forwarding is denied, stop; do not broaden SSH authorization automatically.
3. Start only the new permission service and the separate archive relay.
4. From an isolated compatible worker, verify the socket owner/group/mode, actual
   `/available` responses and complete checksum-verified `/read` responses for
   the current Direct bucket. Verify reconnect and refusal of unavailable data.
5. Keep the worker offload flag false until compatible readers, private metadata
   backup/restore, unsealed archive copies and worker handoff are complete.
6. Enable bounded offload in the compatible worker and start a fresh full 24-hour
   observation. Require actual per-sensor persisted progress and successful retry
   handling, rather than process liveness alone.

Only the new relay may be stopped during transport rollback. The original source
link and reception services must stay active. After any references exist, retain
compatible assemblers and readers even if further offload is disabled.

Storage Box originals remain permanent. Local WAV deletion, raw retention and
visual pruning remain disabled throughout this rollout.
