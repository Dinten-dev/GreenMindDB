# Explicit empty compressed storage in isolated restores

Universe-v1 is unchanged: private compression roots remain unknown relations and
block. Universe-v2 is opt-in and adds exactly `empty_compressed_storage` to the
existing `version`/`tables` keys. Both existing restore plan-head versions may
contain this explicitly versioned universe. No archived input is rewritten.

The operator first restores authenticated schema/master/provider artifacts into
its NEW isolated target, with source-captured background jobs quarantined. Before
loading application data, call `restore_compressed.inventory(connection)` to
obtain reviewable TARGET metadata. This is NOT authorization. Independently
compare all names, logical public parents and compressed catalog links against
the reviewed source schema TOC/provider structure. Unknown names/links block;
never copy source OIDs into target identities. Freeze the complete resulting plan
in the existing private root-owned immutable-input flow and independently pin
its SHA. Do not automatically exempt every object returned by discovery.

Each entry contains exactly:

- `schema`, `name`, `oid`, `kind` (`root` or `chunk`);
- `public_table`, `public_oid`, `hypertable_id`;
- `compressed_hypertable_id`, `root_oid`;
- `chunk_id`, `original_chunk_id`, `relation_sha256`.

Root entries use `oid == root_oid` and null chunk IDs. Chunk entries require both
catalog links (original chunk to compressed chunk, compressed chunk to compressed
hypertable) and actual `pg_inherits` linkage to that exact root. Every entry must
map to a planned PUBLIC DATA-role hypertable. Identities are sorted by OID and
bounded to 64 roots plus 256 chunks. Storage must be permanent ordinary tables in
`_timescaledb_internal`, with row security disabled. This schema condition is
additional validation, NEVER a namespace exemption.

Every bind and every transaction-ending session/baseline/load/audit path checks:

1. Exact native Timescale 2.17.2 catalog provenance; READ COMMITTED visibility.
2. Exact identity, catalog links and stable relation-catalog SHA.
3. SHARE NOWAIT locks on EACH physical relation, held through commit.
4. Fresh identities after locking, then `SELECT 1 FROM ONLY ... LIMIT 1` empty.
5. The same checks after this call's own DML and before its commit.

Only those exact checked OIDs are removed from the otherwise strict relation
inventory. Unknown private relations, omitted chunks, altered links/catalogs,
row-security changes or any visible private payload row fail closed. Source
triggers are preserved: a trigger which writes private payload causes the entire
load transaction to roll back. Inherited public chunks retain the existing
mutation-fence coverage. No triggers, roles or settings are weakened.

"Empty" means zero live visible tuples in EACH locked physical relation; it does
not claim zero allocated pages, forensic removal of dead tuples, continuous
cross-call observation, or restoration of compressed payload. Other transactions
cannot write while the SHARE locks are held. The existing trusted-target-owner
and no-application-writers contract remains required across calls; privileged
owner compromise is outside that contract.

Local tests cover metadata/identity rejection, locking order, bounds, unchanged
v1, real SQLite rollback for trigger side effects, replay, and final typed audit.
SQLite catalog/lock adapters are explicitly synthetic. Actual PostgreSQL 15 /
Timescale 2.17.2 end-to-end capture/readback/restore remains required before a
native-success or production-readiness claim. No source mutation, native action,
new archive format or deployment is performed by this patch.
