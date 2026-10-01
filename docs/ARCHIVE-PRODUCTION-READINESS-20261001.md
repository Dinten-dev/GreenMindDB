# Production readiness: dashboard history and original-WAV exports

This is an acceptance record, not an activation record. The authenticated
dashboard/export routes and their worker have not been switched into Production.
The active WAV copier remains copy-only, and no original has been deleted for
this release.

## Verified on 2026-10-01

- Gateway `127.0.0.1:8120/health`, Direct `:8003/health`, and Legacy
  `:8000/health` each returned healthy while the existing copy service ran.
- The private copy journal reported 116,815 verified files and 52,421,429,650
  bytes at the last bounded audit. The production monitoring metric reported
  148,766 verified Gateway WAV features and 66,754,298,930 bytes of RAW-WAV
  metadata. These are different grains and do not establish the precise
  outstanding file count. Full source-to-archive reconciliation is still open.
- A Gateway and a Direct WAV were downloaded independently from the Storage Box.
  Each was 456,044 bytes, matched its journal SHA-256, and decoded as mono,
  16-bit, 380 Hz PCM with 228,000 frames.
- The active copy launcher forces `RAW_ARCHIVE_DELETE_ENABLED=false` and
  `RAW_ARCHIVE_READS_ENABLED=false` before starting its worker.
- Local release validation: 367 backend tests passed, with 11 skipped and 39
  marked tests excluded; Next.js production build, frontend type/lint/format,
  Ruff, and production dependency audits passed. GitHub CI for commit
  `3ededc6ca7507bde92fb0d655e0774e5de4cef7f` passed all four jobs and
  GitGuardian in PR #3. A later resource-pause fix needs repeat CI.
- The active host had 569 MiB MemAvailable and 571 MiB unused swap while the
  copier was active. Starting an additional API/frontend/export-worker set is
  not yet demonstrated safe for continuous copying or ingestion.

## Gates for the requested live switch

1. Reconcile current Gateway and Direct source catalogs against verified archive
   receipts by immutable object identity and bytes. Continue the existing
   protected copy schedule until the eligible backlog is zero, while new data
   still arrives. Repeat independent readbacks across old and recent days.
2. Provision a read-only Storage Box subaccount and read-only object-store and
   database credentials. Make the archive journal visible to the new read
   service without giving that public service write access to the copier's key
   or journal. Install one private shared export directory and a restricted
   worker identity.
3. Build candidate images away from Production. Measure memory and p95 response
   time for 1 hour, 7 days, 30 days and long history in Staging. Verify zone
   authorization, CSV/display agreement, original ZIP contents, expiry,
   cancellation, and retry after low-resource pauses. The planned hour/day
   materialization and response cache remain unimplemented.
4. Rehearse Nginx-only traffic switching against the actual layered Production
   site. Preserve the Gateway continuity guard, Direct and Legacy receiver
   routes, administration overlays, and static-asset fallback. Record the
   previous config and rollback. Confirm adequate RAM before starting any
   additional candidate container on this 4 GiB host.
5. After switch, compare receiver start times, Gateway/Direct ingest counters,
   WAV/feature arrivals, health, dashboard and downloads at multiple intervals.

Local deletion is outside this release. Independent snapshots, a seven-day local
grace period and a snapshot-aware deletion guard remain future requirements;
never enable deletion based on these readback samples alone.
