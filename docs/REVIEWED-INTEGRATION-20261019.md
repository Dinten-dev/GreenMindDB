# Reviewed source integration — 19 October 2026

This integration brings the prepared archive code and the independently qualified
Direct receiver repair into the same repository as current `main`. It does not
activate production services, change sensor state or authorize deletion.

## Included source

- Prepared archive parent: `969a2fe0663062076d7f820d726005d665d5a107`.
- Combined review parent: `f02e4245c80194c1a643efa0c3242382647c563f`.
- Qualified receiver repair: `3c49a02feea43d1fc952cfb7333327485d061a21`.
- Current main, including isolated OTA delivery: `518a25e6b1cfb56708dcc545c8d7eb8e36e6646b`.

The receiver changes keep WAV/object-storage reads outside receive locks, release
SQL payloads only after independent matching Source and Archive reconstruction
when archive-required mode is enabled, preserve reference-capable readers and
hold-worker rollback, cycle bounded candidates, and distinguish archive deferral
from receiver failure. Raw Direct recordings remain unchanged; HPK comparisons
are separate authenticated derived views.

## Disabled activation and permanent originals

Legacy/Direct retention defaults remain disabled and dry-run. Raw archive deletion,
archive processing and visual pruning remain opt-in and disabled by default.
No production activation, deployment workflow or firmware update is dispatched
by this integration. Existing configured receiver services are not changed.

The Storage Box is for permanent original WAVs only. New catalog and recovery
metadata uploads through the StorageBox adapter fail before any transport call.
Existing legacy metadata download interfaces remain for recovery compatibility;
existing remote files are neither removed nor rewritten. Keep current metadata,
snapshot JSONL and database backups on private hosts. Historic instructions that
publish catalog/recovery metadata to the Storage Box are superseded by this rule
and must not be used. The former metadata-on-StorageBox deletion proof workflow
cannot be activated through this adapter. A reviewed private-metadata proof backend
and complete operational gates are still prerequisites for any future activation.

## Evidence scope and recovery

The existing C4/C6 repair has a separate 13–14 October production24h acceptance:
1440 checks, no observed receive5xx/locks/worker errors or quota mismatches. This
is not a24h qualification of this combined head. The f02 native isolated synthetic
run matched182 module hashes and reconstructed760000bytes for five synthetic
sensors; it is not a production backup or full restore. Current originalreadback,
all-range UI/CSV/peaks, full archive inventory, consistent backup and isolated
restore remain operational gates. Copy-backlog progress is not complete archival.

Code review, tests and GitHub merge do not grant production deployment. Preserve
the functioning receiver and the exact compatible reader/hold-worker rollback
artifacts; do not roll back a database containing payload references to code that
cannot reconstruct them. No production migration or counter reset is performed.
