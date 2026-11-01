"""Explicit, empty-only Timescale storage contract for a NEW isolated restore DB.

This does not restore compressed payloads. Independently pinned target identities
must be acquired after schema/provider restore, before any application data load.
The trusted target owner and exclusive operator remain required across calls.
"""

import re

from sqlalchemy import text

from app.raw_archive.policy import ArchiveBlocked

MAX_STORAGE = 320  # At most 64 compressed roots plus 256 physical chunks.
FIELDS = {
    "schema",
    "name",
    "oid",
    "kind",
    "public_table",
    "public_oid",
    "hypertable_id",
    "compressed_hypertable_id",
    "root_oid",
    "chunk_id",
    "original_chunk_id",
    "relation_sha256",
}
POSITIVE = {
    "oid",
    "public_oid",
    "hypertable_id",
    "compressed_hypertable_id",
    "root_oid",
}
NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}")


def require(value, reason):
    if not value:
        raise ArchiveBlocked(reason)


def validate(value, tables):
    require(
        isinstance(value, list) and len(value) <= MAX_STORAGE,
        "Bounded explicit compressed-storage identities required",
    )
    for row in value:
        require(
            isinstance(row, dict)
            and set(row) == FIELDS
            and all(type(row[key]) is int and 0 < row[key] < 2**32 for key in POSITIVE)
            and row["schema"] == "_timescaledb_internal"
            and isinstance(row["name"], str)
            and NAME.fullmatch(row["name"]) is not None
            and isinstance(row["public_table"], str)
            and tables.get(row["public_table"]) == "data"
            and row["kind"] in {"root", "chunk"}
            and isinstance(row["relation_sha256"], str)
            and re.fullmatch(r"[a-f0-9]{64}", row["relation_sha256"]) is not None,
            "Exact planned public-hypertable compressed identity required",
        )
        if row["kind"] == "root":
            require(
                row["oid"] == row["root_oid"]
                and row["chunk_id"] is None
                and row["original_chunk_id"] is None,
                "Compressed root identity mismatch",
            )
        else:
            require(
                all(
                    type(row[key]) is int and 0 < row[key] < 2**31
                    for key in ("chunk_id", "original_chunk_id")
                ),
                "Compressed chunk linkage required",
            )
    require(
        len({row["oid"] for row in value}) == len(value)
        and len({(row["schema"], row["name"]) for row in value}) == len(value)
        and value == sorted(value, key=lambda row: row["oid"]),
        "Compressed identities must be unique and ordered",
    )
    roots = {row["oid"]: row for row in value if row["kind"] == "root"}
    require(
        len(roots) <= 64 and len(value) - len(roots) <= 256,
        "Compressed root/chunk metadata budgets exceeded",
    )
    require(
        all(
            row["root_oid"] in roots
            and all(
                row[key] == roots[row["root_oid"]][key]
                for key in (
                    "public_table",
                    "public_oid",
                    "hypertable_id",
                    "compressed_hypertable_id",
                )
            )
            for row in value
        ),
        "Every compressed chunk requires its exact planned root",
    )


# TS2.17.2 compressed storage is linked through its catalogs, not ordinary
# pg_inherits. Require the exact standalone physical shape as well: an added
# inheritance edge must never silently alter the pinned empty-storage universe.
CATALOG = """WITH roots AS (
 SELECT h.id AS hypertable_id,h.compressed_hypertable_id,h.table_name AS public_table,
 p.oid::bigint AS public_oid,r.oid::bigint AS root_oid,
 r.oid::bigint AS oid,n.nspname AS schema,r.relname AS name
 FROM _timescaledb_catalog.hypertable h
 JOIN _timescaledb_catalog.hypertable z ON z.id=h.compressed_hypertable_id
 JOIN pg_catalog.pg_namespace pn ON pn.nspname=h.schema_name
 JOIN pg_catalog.pg_class p ON p.relnamespace=pn.oid AND p.relname=h.table_name
 JOIN pg_catalog.pg_namespace n ON n.nspname=z.schema_name
 JOIN pg_catalog.pg_class r ON r.relnamespace=n.oid AND r.relname=z.table_name
 WHERE h.schema_name='public' AND p.relkind='r' AND p.relpersistence='p'
   AND n.nspname='_timescaledb_internal' AND r.relkind='r' AND r.relpersistence='p'
   AND NOT r.relrowsecurity AND NOT r.relforcerowsecurity
), storage AS (
 SELECT roots.*,'root'::text AS kind,NULL::integer AS chunk_id,
 NULL::integer AS original_chunk_id FROM roots
 UNION ALL
 SELECT b.hypertable_id,b.compressed_hypertable_id,b.public_table,b.public_oid,b.root_oid,
 c.oid::bigint AS oid,n.nspname AS schema,c.relname AS name,'chunk'::text AS kind,
 cc.id AS chunk_id,ch.id AS original_chunk_id
 FROM roots b JOIN _timescaledb_catalog.chunk ch ON ch.hypertable_id=b.hypertable_id
 JOIN _timescaledb_catalog.chunk cc ON cc.id=ch.compressed_chunk_id
   AND cc.hypertable_id=b.compressed_hypertable_id
 JOIN pg_catalog.pg_namespace n ON n.nspname=cc.schema_name
 JOIN pg_catalog.pg_class c ON c.relnamespace=n.oid AND c.relname=cc.table_name
 WHERE NOT ch.dropped AND NOT cc.dropped AND n.nspname='_timescaledb_internal'
   AND c.relkind='r' AND c.relpersistence='p'
   AND NOT c.relrowsecurity AND NOT c.relforcerowsecurity
   AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_inherits i WHERE i.inhrelid=c.oid)
) SELECT * FROM storage ORDER BY oid LIMIT :limit"""


def inventory(db):
    """Reviewable target metadata, not authority to add arbitrary plan exemptions."""
    from .restore_fence import _relation_pin
    from .restore_universe import chunk_exemption

    require(
        db.dialect.name == "postgresql" and bool(chunk_exemption(db)),
        "Compressed storage requires exact native Timescale provenance",
    )
    require(
        db.execute(text("SHOW transaction_isolation")).scalar_one() == "read committed",
        "Fresh compressed-storage visibility requires READ COMMITTED",
    )
    rows = [dict(row) for row in db.execute(text(CATALOG), {"limit": MAX_STORAGE + 1}).mappings()]
    require(len(rows) <= MAX_STORAGE, "Compressed storage exceeds metadata budget")
    for row in rows:
        row["relation_sha256"] = _relation_pin(db, row["name"], row["schema"])
    return rows


def check(db, plan):
    """Lock and verify every explicitly pinned private physical relation is empty.

    Called both on transaction admission and immediately before commit. SHARE locks
    exclude concurrent ordinary writers/DDL until commit. The second check catches
    writes from the operator's own transaction, including source trigger effects.
    """
    value = plan["universe"]
    if value["version"] == 1:
        return ()
    expected = value["empty_compressed_storage"]
    validate(expected, value["tables"])
    require(inventory(db) == expected, "Compressed storage catalog or identity changed")
    for row in expected:
        qualified = f'"{row["schema"]}"."{row["name"]}"'
        db.execute(text(f"LOCK TABLE ONLY {qualified} IN SHARE MODE NOWAIT"))
    require(inventory(db) == expected, "Compressed storage changed while locking")
    for row in expected:
        qualified = f'"{row["schema"]}"."{row["name"]}"'
        require(
            db.execute(text(f"SELECT 1 FROM ONLY {qualified} LIMIT 1")).first() is None,
            "Compressed restore storage must remain physically empty",
        )
    return tuple(row["oid"] for row in expected)
