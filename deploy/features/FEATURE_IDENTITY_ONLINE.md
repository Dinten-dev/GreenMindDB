# Explicit online feature identity capability

The actual production database is on Alembic `0023`; authentication tables from
`0024` are absent. Its feature table has approximately 222,144 rows and 518 MB
total size. Those are inventory estimates, not an index-build benchmark.

Do not run Alembic `upgrade 0025` to obtain the feature capability: that requests
the separate authentication migration. The explicit feature operation below
keeps the Alembic head and authentication catalog unchanged. No deployment or
DDL authorization is implied by this document.

## Contract

`app.raw_archive.feature_identity_upgrade` provides:

* `review(sql, expected_database=..., expected_heads=("0023",))`: catalog-only
  review, yielding a plan which the caller stores privately and immutably.
* `prepare(sql, plan, checkpoint=..., journal=..., repair_invalid=False)`: build
  the six-column unique index concurrently while the four-column constraint
  continues protecting the table. Reuse only an exactly compatible valid index.
* `switch(sql, plan, checkpoint=..., journal=...)`: acquire an immediate
  `ACCESS EXCLUSIVE NOWAIT` lock, validate again, atomically replace the old
  constraint using the prepared index, and commit. Any pre-commit failure rolls
  back the entire switch. A committed switch is never automatically downgraded.
* `completed(sql)`: catalog-only acknowledgement of the exact six-column
  constraint. Alembic `0025` calls only this function; its existing graph edge to
  `0024` remains intact. Fresh databases also require explicit preparation before
  `0025` can complete. No head stamping or fake migration revision is performed.

The columns are `wav_file_id`, `extractor_version`, `calibration_version`,
`parameter_hash`, `source_sha256`, `feature_checksum`. No business rows are
deleted, updated, inserted, deduplicated or copied. Foreign keys and other
constraints are fingerprinted and preserved. No authentication rows are read.

Use an existing direct primary PostgreSQL 15 owner connection. Transaction
pooling is rejected by backend-PID checks. Session-level two-integer advisory
authority serializes operators and uses a different namespace from the WAV
single-bigint work leases. No new role or permission is required or granted.

## Failure and resumption

The controller supplies the unchanged source-health/resource checkpoint. A
health failure pauses forward progress. The async adapter polls it during SQL
waits and requests cancellation. Rollback and authority release remain possible
after that checkpoint fails. Connection closure never commits a transaction.

Concurrent work has a 150 ms lock timeout, 120 second server statement timeout,
16 MB maintenance work memory and zero parallel maintenance workers. The switch
uses NOWAIT plus 150 ms lock, 2 second statement and 2 second idle-in-transaction
timeouts. These last two are per-statement/idle bounds, not a claim that all
metadata reads plus the transaction finish within two seconds.

The PostgreSQL backend lies outside the controller's cgroup. The production
caller must budget it explicitly, record its backend/actor identity, retain the
existing ingestion-health gates, and prove terminal cancellation after external
controller termination. This module does not establish live throughput or
production index latency. Hardware/resource limits must not be silently raised.

`DirectoryJournal` uses a private 0700 directory and 0600 JSONL file, exclusive
file lock, bounded writes and fsync. It records the plan hash and build intent
before DDL. Preserve the exact original plan across retries: catalog estimates
may change while data ingestion continues. A valid completed index is reused
after a controller crash. An invalid index is left in place by default. It can
be removed concurrently only with explicit `repair_invalid=True`, an exact
matching owned build intent, the reserved index's exact reviewed shape, no
dependencies and the original healthy four-column protection. The old index is
never the repair target.

After an ambiguous commit, obtain fresh catalog evidence. An exact six-column
constraint is idempotently acknowledged, not rebuilt. Journal proofs contain
constraint/index OIDs and catalog hashes, never credentials or application rows.

## Required native acceptance

Local regression tests cover shape drift, invalid-build ownership, resume,
NOWAIT rollback, post-ALTER drift, backend/DB/head/type pins, cancellation and
durable private proof. They do not replace PostgreSQL DDL acceptance.

First run the exact module against a separately marked synthetic staging
database, at most five fixture rows, including real invalid CIC, interrupted
prepare, lock conflict, explicit repair, uniqueness semantics and row preservation.
Check database owner/marker immediately before each DDL. Preserve native
terminal and before/after catalog evidence. The earlier three-row TEMP test
exercised the old `0025` implementation; it proves neither this concurrent
algorithm nor production index runtime. A production DDL scope and measured
resource/lock envelope remain separate prerequisites.

PostgreSQL references:

* [Concurrent index creation](https://www.postgresql.org/docs/15/sql-createindex.html)
* [Constraint adoption and locking](https://www.postgresql.org/docs/15/sql-altertable.html)
* [Index validity and structure](https://www.postgresql.org/docs/15/catalog-pg-index.html)
