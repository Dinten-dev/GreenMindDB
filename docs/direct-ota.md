# Direct-to-Cloud OTA sidecar

This service adds device-scoped firmware delivery without changing ingest,
assemblers, visualization/archive readers or database schemas. Direct firmware
must first be bootstrapped over USB; old devices cannot install OTA remotely.
Only the Biolingo v22 N16R8 DIRECT profile is currently supported.

## Runtime

Start `uvicorn app.direct.ota_service:service --factory --host 0.0.0.0 --port 8004
--proxy-headers --forwarded-allow-ips <exact-proxy-bridge-ip> --no-access-log`.
Bind the published port to host loopback only. Reverse-proxy only
`/api/v1/direct-ingest/ota/` over the existing Production HTTPS origin. Set
`X-Forwarded-Proto` from the proxy's actual scheme; never trust arbitrary clients.
Use a separate worker, one database connection, bounded memory and CPU. The
existing device token authenticates against `direct_device`; the DB connection
is released before file I/O. No Gateway credentials or cross-server token copies.

Environment: `DIRECT_DATABASE_URL` (existing dedicated Direct database),
`DIRECT_OTA_ROOT`, `DIRECT_REQUIRE_TLS=true`. Do not log environment values,
headers or tokens. TLS is mandatory at the public proxy. No redirect to GitHub.

Mount root-controlled catalog/images read-only. Catalog directories use mode
2750 with the service's numeric group; catalog/image files use mode 0640. Give
the separate reports directory to the service UID/group, mode 0700, mounted
read/write. The CLI atomically creates group-readable catalog files, with no
world access. Do not run a writer inside the public ASGI app.

## Explicit local control

Run the CLI on the same source host, with administrative filesystem access:

```bash
python -m app.direct.ota_admin --root /var/lib/greenmind/direct-ota stage --image firmware.bin --version direct-hotspot-v2.10-rc1-production --sha256 EXACT_RELEASE_SHA256
python -m app.direct.ota_admin --root /var/lib/greenmind/direct-ota assign --device DEVICE_UUID --from-version EXACT_RUNNING_VERSION --sha256 EXACT_RELEASE_SHA256 --expires-hours 24
python -m app.direct.ota_admin --root /var/lib/greenmind/direct-ota clear --device DEVICE_UUID
```

Stage only physically accepted, release-checksum-verified application binaries.
An assignment requires one exact device and source version, expires, and uses a
new command UUID. A downgrade is only an explicit source/target assignment.
Default empty catalog/device map returns no updates. GitHub's `direct-latest`
does not automatically become the fleet's desired version. `assign`/`clear` are
local administrator operations, never public API routes. Administrative CLI
writes are serialized and durable; the service reads atomic snapshots. Immutable
command history keeps late boot reports valid after expiry, replacement or
clearing an assignment; history never authorizes a download or installation.

## Wire contract

- `GET .../check?profile=...&current_version=...`: 204 unless an eligible,
  unexpired device assignment matches; otherwise bounded JSON with device,
  profile, command UUID, exact source/target versions, SHA-256 and byte count.
- `GET .../download/{command_id}`: same authenticated device only; fully reads
  and hash-validates the immutable ESP32-S3 image before returning exact bytes
  and Content-Length. Symlink, traversal, wrong-chip, corrupt/oversized/missing
  images fail closed. No network storage calls or DB locks during image reads.
- `POST .../report`: bounded, authenticated identity/profile/version/status
  report, persisted as a private append-only audit line. `staged` requires an
  empty sensor spool; `booted` requires the target version and ten successful
  verified upload acknowledgements. Wrong identities/versions are rejected.

The sensor pauses acquisition before flash writes, flushes its partial block,
and drains the queue with the existing identity/hash/sequence-checked upload
protocol. Failed drain aborts the update; failed length/hash/image/read-back
verification leaves boot selection unchanged. Recording resumes with an explicit
elapsed-time gap. Successful flash selection occurs only after a complete
independent flash read-back and persisted pending command/version.

The included bootstrap bootloader supports pending-trial rollback. The Arduino
early automatic confirmation is postponed until real acquisition/upload checks.
A rejected trial rolls back; the restored sensor reports that result. A planned
update/reboot is a maintenance gap. Captured volatile PSRAM contents are not
power-loss persistent, including during unsuccessful trial boots.

## Rollout and recovery

Before fleet assignments, install and test one sensor over USB, then prove
cloud OTA both directions, a bad-hash rejection and rejected-trial rollback.
Review `reports/<device>.jsonl`, sensor status/firmware and actual upload progress.
Clear the completed test assignment. Other device assignments stay empty until
their bootstrap and operating checks are complete. Publishing a USB ZIP is not
proof that remote legacy sensors have upgraded.

Rollback of the OTA service: clear assignments, remove its dedicated proxy
location after validating the proxy config, then stop only the sidecar. Existing
ingest/archive readers and their compatible reconstruction-reference versions
must remain running. OTA firmware remains operational when the sidecar is down.
Never change archive retention/deletion settings as part of an OTA rollout.
