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

## Build and preparation

1. Finish and commit the reviewed source. Run all CI jobs, including PostgreSQL
   archives/migrations and the real local nginx continuity test. The manual
   **Prepare Staging Release** / **Prepare Production Release** workflows require
   the full 40-character successful CI commit. Alternatively run
   `bash deploy/release/build-bundle.sh /absolute/new/output` on a build workstation.
   The source must be clean. No build is performed on the receiving server.
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
