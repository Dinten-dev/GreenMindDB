# Archive downloads and bounded visualization — prepared release

This package is source-only. `ARCHIVE_EXPORTS_ENABLED`,
`NEXT_PUBLIC_ARCHIVE_EXPORTS_ENABLED`, `RAW_ARCHIVE_READS_ENABLED`,
`RAW_ARCHIVE_READS_ACCEPTED`, and `RAW_ARCHIVE_DELETE_ENABLED` remain false in
the examples. No receiver, database, reverse proxy, or archive policy is
activated by copying these files to a server.

## Implemented

- Gateway and Direct display/CSV endpoints share a bounded resolution selector;
  existing explicit resolutions remain available. The selected window can be
  sent to CSV export so it describes the displayed period.
- Historical records may be read through the prepared verified archive reader;
  an archived Gateway WAV is listed as available only with a local verified
  receipt and enabled read flag. Tenant/zone checks precede reads.
- An authenticated export-job API queues original Gateway/Direct WAVs from
  authorized catalogs. An isolated worker verifies each SHA-256 and byte size,
  writes ZIP parts and ML metadata, then exposes parts only after the whole
  job succeeds. Jobs expire after 24 hours.
- A disabled-by-default dashboard panel creates jobs and displays progress.
  Existing single-WAV downloads remain available.

## Validation

- 82 focused backend tests passed; 11 integration tests were skipped because
  local PostgreSQL was unavailable; 17 unrelated marked tests were deselected.
- TypeScript type check, changed-file ESLint, three affected UI tests, and a
  production frontend build with the webpack builder passed. The default
  Turbopack build could not bind a local port in this sandbox.
- The job worker was tested for byte-identical ZIP contents and atomic failure
  on a checksum mismatch. This is not a substitute for an actual Storage Box
  restore drill.

## Gates before any live switch

1. Build a dedicated read-only Storage Box subaccount and a shared private
   export directory for one unprivileged API/worker UID. Provide read-only
   Gateway/Direct object-store credentials and the existing read-only DB role.
2. Verify real receipt paths, full archive download, WAV decode, and manifest
   SHA-256 for Gateway and Direct on Staging. The archive reader needs its
   existing private journal and pinned SSH host key.
3. Verify dashboard and CSV for 1 hour, 7 days, 30 days and all history with
   production-like data. Measure p95 query time, query plans, peak RAM, gateway
   and Direct ingestion. The aspirational p95 thresholds are 2 seconds cold,
   500 ms cached, <=1200 points per series. Hour/day materialized caches and
   the 32 MiB response cache are **not implemented yet**; do not claim the
   performance gate passed until these are built and measured.
4. Install the worker as a separate restricted systemd unit, wire a private
   Nginx route to the authorized read sidecar, and activate feature flags only
   after routing and rollback rehearsal. Do not change receiver containers.
5. An independently recorded Storage Box snapshot, snapshot-aware deletion
   guard, signed/index backup, and a seven-day local grace period are **not
   implemented**. Keep local deletion disabled. Storage Box snapshots alone
   are not an independent second copy.
6. Before Production, review the exact source diff and package hash, repeat
   tests on Staging, perform a real restore, and confirm a rollback that only
   disables new read/export routes. Production approval is separate.

These open gates are intentional release blockers. This package must not be
described as ready for deletion or as meeting the long-range performance SLO.
