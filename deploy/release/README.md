# Isolated release procedure

This replaces the former automatic full-stack rollout for the current additive
Direct/dashboard release. A push only runs CI. Manual GitHub workflows build an
immutable package after CI succeeds for the exact commit; they do not connect to
any server. The operator and user review the exact package and destination before
transfer. Production remains a separate approval.

## Invariants

- The old Gateway API, login, WebSockets, WAV assembler, PostgreSQL and MinIO remain
  running. Direct reception is a separate project and is not restarted here.
- A fresh loopback API/frontend pair is started before public traffic changes.
  Previous frontend assets remain available for already open browsers.
- Images must be loaded config IDs (`sha256:...`) with matching source labels.
  Configuration files, current proxy and source revision are checked for drift.
- Secret files are private, never included in the package or printed. The read
  service uses deployed authentication validation without mail/signing secrets.
- Projection workers have bounded resources and explicit, reversible handover.
  They never run with `--prune` in this procedure. Existing archive paths remain
  readable; original source rows and WAVs are not removed by this release.
- Production activation requires an exact-image, exact-commit Staging acceptance
  record. Missing hardware/UI/rollback evidence is a blocker, not a checkbox that
  the program fills in automatically.
- A failed nginx check, reload or authenticated public check restores the previous
  proxy. Rollback to the old non-visual API is refused if compacted chunks exist.

## Keep the existing Raspberry unchanged

The cloud can issue app/config updates, reboot/service commands and a legacy
heartbeat or ingest reset response. A server release must not deliver any of those actions.
Before preparing a release, install the reviewed nginx continuity guard. This
changes only the cloud proxy; do not update, reboot, reinstall or disconnect the
production Raspberry. Its existing local health watchdog remains unchanged.

After the user's review and approval of the exact server-only diff:

```sh
python3 deploy/release/gateway_guard.py prepare --environment staging --directory /home/traver/gateway-guard-staging-REVIEWED_DATE
# Review before.conf, proposed.conf and manifest.json first.
sudo python3 deploy/release/gateway_guard.py activate --environment staging --directory /home/traver/gateway-guard-staging-REVIEWED_DATE
```

Production uses `--environment production` and its own fresh directory, after
separate approval. Before activation, check that no device update/reboot is
already executing: a command fetched earlier cannot be revoked by a proxy.

The guard returns HTTP 503 for desired-state polls, remote command polls and
app/config downloads. Existing gateway workers retry these responses without
exiting or resetting. Heartbeat and ingest requests still reach the old API;
their HTTP 410 responses are converted to HTTP 503, so a backend registration
problem cannot trigger the old firmware's `RESET_TO_SETUP_MODE` handlers.
The installed v1.0.9 was inspected read-only on 2026-09-21: both handlers exist.
Upload destinations, authentication, successes and other error responses stay unchanged.
No upload is falsely acknowledged. TLS/DNS outages still appear as network errors.

The full-stack maintenance script also checks the installed guard before rsync or builds.
`release.py` refuses preparation or operations without the exact guard in both
current/rollback and candidate configurations. A visualization rollback therefore
keeps remote control paused. The standard site templates include the same guard.
Do not queue remote actions during this pause; removing it is a separate reviewed
Gateway-maintenance action, including inspection of pending commands/targets.

Only one physical Raspberry is available. Exercise failures in local simulations,
not on that device: continue real local ingestion/health during DNS, timeout,
401/403 and 502/503 errors, retain queued readings/WAVs, then recover uploads.
Validate the installed Pi version read-only when its SSH identity is confirmed.
The inspected v1.0.9 keeps running after ordinary cloud failures, but moves
aggregate jobs to its dead-letter queue after more than 20 failed attempts;
these need separate replay. Its WAV uploader retains files on failure but
accepts HTTP 200/201 without verifying a checksum-bearing acknowledgement.
The current repository's stronger queue/ACK tests do not prove those properties
for the unchanged installed release. Do not update the only production Pi as
part of this server rollout or claim lossless automatic recovery for v1.0.9.
These checks do not prove unlimited buffering, power-loss survival, or immunity
to a local disk/hardware failure. They do not authorize a Production rollout.

## Build and preparation

1. Finish and commit the reviewed source. Run all CI jobs, including PostgreSQL
   archives/migrations and the real local nginx continuity test. The manual
   **Prepare Staging Release** / **Prepare Production Release** workflows require
   the full 40-character successful CI commit. Alternatively run
   `bash deploy/release/build-bundle.sh /absolute/new/output` on a build workstation.
   The source must be clean. Image contexts come from `git archive` of that exact
   commit, excluding ignored local credentials/test databases. No build is
   performed on the receiving server.
2. Inspect `bundle.json`, its source revision and image/archive hashes. Transfer
   only the reviewed package after user approval. Verify SHA256 before
   `docker image load -i images.tar.gz`. Loaded config IDs must equal `bundle.json`.
3. Ensure the environment's independent Direct receiver is already prepared and
   healthy. For first Production activation, follow `deploy/direct-production/README.md`:
   dedicated credentials/database/bucket, reviewed capacity, immutable image,
   Direct-only TLS/proxy setup and explicit activation. Existing Gateway services
   are not changed. This prerequisite avoids silently copying Staging resources.
4. As the Docker operator, run the following using the actual package revision and
   image config IDs. Choose a **fresh** directory on the archive/root filesystem.
   Example below is Production; rehearse identically with `staging` first.

   ```sh
   python3 deploy/release/release.py prepare --environment production \
     --directory /home/traver/greenmind-release-production/REVIEWED_COMMIT \
     --revision REVIEWED_FULL_COMMIT_SHA \
     --backend-image sha256:REVIEWED_BACKEND_CONFIG_ID \
     --frontend-image sha256:REVIEWED_FRONTEND_CONFIG_ID
   python3 deploy/release/release.py start --directory /home/traver/greenmind-release-production/REVIEWED_COMMIT
   python3 deploy/release/release.py workers --directory /home/traver/greenmind-release-production/REVIEWED_COMMIT
   python3 deploy/release/release.py verify --directory /home/traver/greenmind-release-production/REVIEWED_COMMIT
   ```

   `prepare` copies only relevant runtime values from the correct environment's
   running services, without printing credentials. No public proxy is changed.
   `start` initializes additive visualization tables only and starts API/frontend.
   `workers` replaces only projection workers, restoring their predecessors if
   validation fails. A warming historical backlog can require a later retry.
   The initial Production Direct database may have no devices; this is reported
   explicitly, while Direct storage/worker availability is still required.

## Acceptance and traffic switch

Review `manifest.json`, `nginx.before.conf`, `nginx.proposed.conf`, `compose.json`
and `validation.json`. Never attach or publish `runtime.env`.

On Staging, test a physical old Gateway and a Direct sensor together, sample/WAV
integrity, Direct interruption/retry while Gateway continues, UI waveform/CSV/WAV
access and the rollback command. Record actual observed results, not assumptions:

```json
{
  "environment": "staging",
  "revision": "EXACT_40_CHARACTER_COMMIT",
  "backend_image": "sha256:EXACT_CONFIG_ID",
  "frontend_image": "sha256:EXACT_CONFIG_ID",
  "checked_at": 0,
  "gateway_direct_parallel": false,
  "wav_sample_integrity": false,
  "interruption_recovery": false,
  "rollback_rehearsal": false,
  "ui_review": false
}
```

Replace `checked_at` with the real Unix observation time and set a field to true
only after its test passes. Include logs/device IDs alongside the record. Evidence
expires after seven days and cannot be reused for different images or source.
The supplied example intentionally does **not** authorize a Production rollout.

```sh
sudo python3 deploy/release/release.py activate --directory /home/traver/greenmind-release-production/REVIEWED_COMMIT --acceptance /absolute/path/staging-acceptance.json
sudo python3 deploy/release/release.py rollback --directory /home/traver/greenmind-release-production/REVIEWED_COMMIT
```

Staging `activate` does not require Production acceptance, but runs the same
candidate/public checks and receiver start-time comparisons. Both commands only
alter the reviewed nginx site and issue a graceful reload. If sudo is unavailable,
the operator must run this final step; do not bypass it with a full-stack deploy.
Keep candidate and previous read containers available until acceptance completes.
Retire obsolete read containers only after their ports are absent from both the
active proxy and its static-asset fallback. Never use `--remove-orphans` or volume
removal for this rollout.

## Historical pruning and later maintenance

The first release deliberately leaves historical SQL removal disabled. Dashboard
aggregation and original WAV access still operate. Before a separately reviewed
pruning activation, verify full source archives, checksums and restore/read-back
against the actual Production schema, measure recovery time, and retain the read
API until any required restoration is complete. The existing compaction tooling
in `deploy/visualization` enforces its additional marker/verification checks;
this release script does not manufacture those approvals.

`scripts/deploy.sh --allow-receiver-restart` remains an explicit full-stack
maintenance escape hatch, not a zero-interruption release path. It is never
called by the new release workflows. Updating the Direct receiver itself requires
its own immutable image and separate maintenance/hardware acceptance. A Staging
sensor does not automatically migrate: use the explicit Production firmware
build and a fresh Production pairing code.

Local nginx rehearsal proves HTTP routing continuity and failure rollback under
synthetic ingestion. It does not replace physical Gateway/Direct acceptance,
load testing at the planned fleet size, or certification against a formal norm.
