# Source-side WAV pilot after archive-host offload

This candidate connects the separately hosted copier to a bounded source-side
deletion executor. It does not activate production deletion or bucket lifecycle
rules. The source databases, features and dashboard rows are never deleted.

## Behaviour

1. Existing archive-host copying keeps its own journal and read-only source keys.
2. Preflight builds an exact-set recovery index from current strict source
   records and verified copy receipts. The index now includes those receipts.
   Current feature digests are exported without modifying the copying journal
   or restarting its original seven-day grace clock.
3. Publish the index and complete catalog, verify the empty-database restoration,
   and create the final Storage Box snapshot after publication.
4. `app.raw_archive.pilot` retrieves the hash-pinned index from that snapshot,
   validates every proposed file, restores/checks snapshot proofs, rereads current
   archived WAV bytes, and inspects their exact source versions.
5. Default mode is rehearsal. Execution needs explicit `--execute`, both deletion
   and accepted-reader flags, source role, unexpired human authorization, and
   all existing release/snapshot/catalog/24-hour acceptance gates.
6. A separate source-side journal records `deleting` before the exact-version
   operation and `evicted` after success. Retries cannot expand the named set.
   Failed deletion responses report an uncertain outcome, rather than claiming
   that no object could have been deleted.

The first pilot remains limited to ten WAVs and five MiB. This does not grant
ongoing deletion authority. Execution credentials must be supplied explicitly
using `RAW_ARCHIVE_DELETE_GATEWAY_S3_ACCESS_KEY` and
`RAW_ARCHIVE_DELETE_GATEWAY_S3_SECRET_KEY`, with corresponding `DIRECT` names.
Scope their version deletion privileges to the reviewed object keys. Existing
copying and diagnostic credentials are not upgraded or reused for deletion.

## Commands

Load the existing explicit private source configuration through the service
manager. Run the installed candidate Python with the candidate backend on its
module path, source role and a separate private pilot state directory.

```sh
python -m app.raw_archive.pilot \
  --manifest /PRIVATE/reviewed-pilot.json \
  --sha256 REVIEWED_MANIFEST_SHA256 \
  --state-dir /var/lib/greenmind/wav-pilot-20261014
```

Rehearsal reports `READY_FOR_PILOT_APPROVAL` only after all technical proofs.
An unsigned draft cannot execute. Existing copying journals must not be supplied
as `--state-dir`. Old recovery indexes lacking exported receipts must be rebuilt
and included in a new final snapshot.

`python -m app.raw_archive.rehearsal` runs the initial two-file live readback test
under the archive copier account and its existing source lease/configuration.
It reads one eligible Gateway and one Direct WAV from both the source and
Storage Box, checks lengths/SHA-256 and fully decodes PCM WAV frames. It does not
query production SQL, alter the copying journal, upload RAW files or delete.
`READBACK_TEST_PASSED` is a sample readback result, not deletion acceptance.

## Expiry and receiver protection

Remote proof readbacks can take longer than a manifest's remaining lifetime.
Authorization, accepted release, snapshot and restoration freshness are checked
again afterwards and immediately before removal. The daily deletion path uses
the live clock instead of its initial batch timestamp. Health/headroom and fresh
feature checks run before each operation. A pause or failure retains the source
unless its exact deletion operation had already started, which the journal
records for recovery.

The installer places immutable candidate code under a new hash-addressed
`/opt/greenmind/wav-pilot/` directory. It changes no receiver, Nginx route,
existing environment file, copier schedule, lifecycle or retention flag. Its
archive test runs in one temporary unit with 192 MiB, no swap, 25% CPU, low
priority and a 100-second limit. Busy workers or insufficient headroom return
a pause; do not weaken their guards to force a passing result.

## Actual readiness on 14 October

Administrator read-only checks at 10:46 UTC found current receiver health routes
and the archive reader responding HTTP 200, with deletion/retention disabled.
The latest existing paired observation failed. The complete fresh Gateway
catalog/restoration was unfinished. The provider snapshot last checked earlier
that day was created on 7 October. These are blocking gates, not passing evidence.

After candidate installation and integration acceptance, finish fresh catalog
publication/restoration, establish the accepted reader release, complete a fresh
uninterrupted paired 24-hour observation and fixed-inventory reconciliation,
then obtain and independently restore the final snapshot. Only the resulting
exact pilot manifest can be reviewed for a separate production deletion test.
