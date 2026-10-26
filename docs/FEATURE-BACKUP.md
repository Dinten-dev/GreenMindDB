# Scientific feature backup

The dashboard continues reading the original PostgreSQL data. This separate
backup never moves or deletes PostgreSQL rows, RAW WAVs or earlier backups.

## Coverage

- Gateway: every `wav_feature` and `wav_feature_version`, including scientific
  statistics, quality classifications, extraction/calibration versions,
  parameter hashes and source WAV hashes.
- Direct: every `direct_segment` and `direct_revision`; revision manifests
  retain each run's per-channel extracted features, source key/hash and config.
- This feature bundle excludes accounts, emails, password hashes and tokens.
- Features not yet extracted cannot be backed up. This job does not invoke
  extraction or change the existing extraction workers.
- Dashboard visualization aggregates belong to the separate complete WAV-only
  catalog. A feature bundle does not prove complete catalog recovery or RAW
  deletion readiness.

## Destination and verification

Storage Box directory: `greenmind-raw/production/catalog-backup/`.
Compressed JSONL parts and the manifest use SHA-256 filenames. Existing bytes
must match before reuse. Parts are uploaded to unique temporary names, renamed,
then read back. The manifest is published after all parts.

A separate download reads the manifest and every part again, verifies size and
SHA-256, decodes every row into a new SQLite database, checks per-table counts
and database integrity. Only then is `verified.json` written. Source and local
proofs remain intact after failure. Gateway and Direct snapshots have separate
read-only transactions; they are not an atomic cross-database snapshot.

## Bounds and schedule

`greenmind-feature-backup.timer` starts at 00:00 Swiss time, with retries at
01:00, 02:00 and 03:00. One verified daily backup suppresses later attempts.
The existing shared archive lease prevents concurrent copy/export jobs. A busy
archive lease is retried after twenty seconds, at most three times, to catch
the copier's existing yield window. Memory/load pauses are never retried in
process. Waiting opens no SQL connection. The outer unit budget is 350 seconds;
each active SQL or publication/restore phase retains its two-minute budget.

Worker limits: 128 MiB, no swap, 10% CPU, idle I/O. Source guards remain
640 MiB available RAM, maximum load 2.4, healthy Gateway/Direct/Legacy routes,
and existing disk reserves. SQL export and publication/restore each have a
separate two-minute budget; SQL is disconnected before the second phase.

Upload requires known Storage Box availability, leaving at least 5% and 2 GiB,
plus twice the compressed bundle size. Unsupported quota queries fail closed.
One export is capped at 256 MiB compressed / 512 MiB decoded. Retained local
spools pause new daily attempts above 2 GiB; no automatic cleanup is installed.
Large historical datasets may exceed these bounds and require the approved
isolated archive-host workflow. A paused attempt is not a successful backup.

## Operator status and rollback

Inspect `greenmind-feature-backup.service` journal and the private daily folders
under `/mnt/HC_Volume_106755700/greenmind-feature-backup/`. `verified.json`
identifies the remote manifest and exact restored table counts. An empty table
is explicitly counted as zero rather than inventing feature data.

Disable only `greenmind-feature-backup.timer` to suspend future attempts.
The original copier, receivers, extraction workers, dashboard and reader remain
unchanged. No administrator identity is created. The job reuses the existing
column-limited metadata reader and the existing private copy account's SFTP key;
upload credentials are never mounted into a public reader.

For download, the existing read-only Storage Box subaccount can copy
`production/catalog-backup/` into a new private local directory using rsync over
SSH port 23. The subaccount root is already `greenmind-raw`; do not repeat that
prefix. Preserve the manifest and all referenced parts together. Use the
feature recovery routine against a new empty archive database, never production
application tables.
