# Archive acceptance with bounded memory

Deletion, pruning, source retention and legacy-null removal remain disabled.
This release coordinates archive jobs; it does not authorize removal.

## Runtime changes

- One private host lease covers copying, catalog extraction and ZIP generation.
- Contending jobs exit without opening SQL/S3 clients. Requests expire after five
  minutes. Copying yields between files; exports yield after sixty seconds at
  the next verified WAV boundary. Existing timers retain their schedules.
- The export dispatcher checks the SQLite queue before launching Docker. Its
  existing privileged Docker access runs through a fixed root launcher; the
  actual export container remains UID 996/GID 986, capped at 64 MiB and 10% CPU.
  The dispatcher is capped at 128 MiB. No new administrator account is created.
- The worker imports the small job store without importing API authorization,
  models or SQLAlchemy. Completed ZIP parts are validated and retained across
  retries. Only complete verified parts advance durable progress.
- Partial files and expired exports are retained during acceptance. They consume
  disk; the unchanged disk guard pauses work before capacity becomes unsafe.
- ZIP responses and job ownership/zone authorization retain their existing API
  contract. CSV visualization and ingestion are not changed by coordination.

## Mac catalog verification

The operator helper creates exactly one role named
`greenmind_wav_metadata_<release>`, with two connections, eight-second statements,
150 ms lock timeout, read-only default transactions, no role membership or
administrative attributes, and SELECT on only the named WAV/feature/projection
columns. No users, emails or password hashes are exported. The new role's private
credential file is the only SQL credential allowed onto the verification Mac.

`hold-export.py` takes the archive lease, checks the unchanged 640 MiB/load/receiver
guards, creates a consistent SQLite journal backup and permits a maximum
two-minute SQL export. Its root-owned heartbeat must remain current. The Mac
uses a loopback SSH database tunnel and `app.raw_archive.isolated_catalog`.
Ten-row cursors and gzip parts bound memory. Each database has its own read-only
REPEATABLE READ snapshot; cross-database consistency must be reconciled rather
than claimed atomic. Only the complete export creates `manifest.json`.

The Mac performs full catalog restore and reconciliation into a new SQLite
database. Catalog downloads use the provider's read-only subaccount. Upload
credentials and MinIO operator credentials remain on the server. Publication
uses the existing bounded operator process after hashes and counts agree.

## Release and rollback

Package code by commit and file SHA-256. The installer preserves the live proxy
hash and receiver start times/restart counts. It adds only two archive service
overrides, applied on the next scheduled run. It never interrupts a running job
or receiver. Installation failure moves its overrides into private evidence and
reloads systemd definitions. `acceptance/rollback.py --manifest INSTALLATION`
does the same for a completed installation. It never rolls back the journal.

## Required acceptance before any pilot

1. Run synthetic legacy-null/version/concurrent-write tests against the exact
   production MinIO image. Enable versioning only after successful proof and
   review; confirm real state through the metadata identity. Do not rewrite WAVs.
2. Complete catalog publication, independent readback, empty SQLite restoration
   and fixed eligible-inventory reconciliation. Retain historical quarantine.
3. Accept the new read-only broker and candidate reader: real zone/job isolation,
   old URLs, local/archive-only reads, ZIP/SHA/WAV, CSV/display and actual download
   progress. Never delete originals to force fallback.
4. Rehearse and publish only the current hash-guarded reader overlay. Preserve
   all admin/static fallbacks, Gateway 8120, Direct 8003 and Legacy 8000.
5. Observe the accepted live release for a fresh 24 hours. Earlier observations
   and local tests are not live-release proof.
6. Finalize a catalog/inventory whose cutoff follows observation start. Publish
   the complete restore proof and bounded pilot index, then create the final
   provider snapshot. Restore the entire catalog and all proposed pilot WAVs
   independently from that snapshot. Refresh bucket/provider/readiness proofs.
7. Obtain separately explicit approval naming at most ten files / five MiB.
   Approval, snapshot and restoration freshness guards remain unchanged. A pilot
   does not authorize unlimited automatic deletion.

Insufficient 512/640/704 MiB reserves pause the relevant step. No ingestion,
database or Staging shutdown and no reduced safety threshold is a workaround.
Operator-created final snapshots remain required while only a read-only provider
API token is configured. Do not report deletion readiness before every real gate
passes.
