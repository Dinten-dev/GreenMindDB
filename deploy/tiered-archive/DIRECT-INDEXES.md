# Direct expression indexes

The three definitions match the existing `Source.export` filter, ordering and
keyset tuple exactly. Numeric epoch columns remain unchanged. In particular the
visual-point index includes the original `bucket` tiebreaker after the converted
timestamp, segment and channel. Source queries, cursors, metadata and archive
formats are unchanged.

PostgreSQL 15.10 defines `to_timestamp(float8)` as native
`float8_timestamptz` (OID1158), with the catalog's default immutable volatility.
Expression indexes therefore use the same conversion, including its rounding,
as the existing query; no raw-float cursor conversion is introduced.

Primary sources:

- [PostgreSQL15.10 function catalog](https://raw.githubusercontent.com/postgres/postgres/REL_15_10/src/include/catalog/pg_proc.dat)
- [PostgreSQL15 catalog default and immutable constant](https://raw.githubusercontent.com/postgres/postgres/REL_15_STABLE/src/include/catalog/pg_proc.h)
- [Expression index and IF NOT EXISTS contracts](https://www.postgresql.org/docs/15/sql-createindex.html)

Run only with `psql -X -v ON_ERROR_STOP=1 -f direct-indexes.sql`, outside a
transaction, after separately reviewing actual source load, free space and the
online-index operation's budget. This source change does not authorize any
production DDL. Concurrent creation still consumes source resources.

The script checks actual catalog definitions before creating anything and after
creation. A same-name numeric index, partial index, wrong table/type/key order,
included key, nondefault sort/opclass, invalid/not-ready index or failed old
concurrent build aborts. It neither drops nor replaces anything. Existing partial
`raw_backup_direct_arrival` and `ix_direct_chunk_inline_received` are not reused:
adding their predicates would omit history or offloaded payloads.

`IF NOT EXISTS` alone is not a successful verification. The check must finish
successfully; its final catalog proof must be bound by the deployment operator
before running the archive worker. The runtime's existing valid/ready/name guard
does not independently establish index-definition equivalence. Do not relax it
or reinterpret an old numeric index as this version. Definition drift or an
existing wrong index requires a separate exact maintenance decision; there is no
automatic DROP/REINDEX/rename/replace recovery.

The standalone `direct-indexes-check.sql` requires all three definitions by
default and performs only catalog reads. `direct-indexes.sql` temporarily permits
absent names during preflight, then requires all of them during postflight.
The psql `ON_ERROR_STOP` setting is part of the contract, not optional.

Local static tests tie expression keys to the current Source dataset order. They
do not prove PostgreSQL parsing, immutable catalog state or planner selection.
The opt-in PG15 integration test requires an explicitly provided fresh empty
local `_restore_` database; it uses synthetic rows and performs no source access.
No production DDL, index drop or new server probe is part of this preparation.
