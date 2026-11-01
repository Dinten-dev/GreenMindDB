"""Externally pinned, complete application-table universe for an isolated restore.

Catalog checks read bounded metadata only. Unknown user relations fail closed;
provider-owned extension internals and this helper's exact journal names are not
mislabelled as archived application data.
"""

from sqlalchemy import Column, Integer, MetaData, String, Table, select, text

from app.raw_archive.policy import ArchiveBlocked

from .codec import digest
from .source import DATASETS

OWN_TABLES = frozenset(
    {
        "greenmind_restore_session",
        "greenmind_restore_batch",
        "greenmind_restore_page",
        "greenmind_restore_total",
        "greenmind_restore_baseline",
        "greenmind_restore_audit",
        "greenmind_restore_universe",
        "greenmind_restore_fence",
        "greenmind_restore_fence_table",
    }
)
MAX_TABLES = 64


def allowed_data(source):
    result = {spec.table for spec in DATASETS.values() if spec.source == source}
    if source == "direct":
        result.update({"direct_piece", "direct_chunk_payload_ref"})
    return result


def validate(plan):
    import re

    value = plan["universe"]
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "tables"}
        or type(value["version"]) is not int
        or value["version"] != 1
        or not isinstance(value["tables"], dict)
        or not 1 <= len(value["tables"]) <= MAX_TABLES
        or any(
            not isinstance(name, str)
            or re.fullmatch("[a-z][a-z0-9_]{0,62}", name) is None
            or name in OWN_TABLES
            or role not in {"data", "master"}
            or (role == "data" and name not in allowed_data(plan["source"]))
            or (role == "master" and name in allowed_data(plan["source"]))
            for name, role in value["tables"].items()
        )
        or value["tables"].get(DATASETS[plan["dataset"]].table) != "data"
    ):
        raise ArchiveBlocked("Exact independently pinned application universe required")


def relation_inventory(db):
    if db.dialect.name != "postgresql":
        raise ArchiveBlocked("Native restore-universe catalog requires PostgreSQL")
    # Ordinary views have no stored rows. Foreign tables and materialized views
    # remain visible here and cannot silently escape the fence's capability gate.
    query = text("""SELECT n.nspname AS schema,c.relname AS name FROM pg_catalog.pg_class c
      JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
      WHERE c.relkind IN ('r','p','m','f') AND left(n.nspname,3)<>'pg_'
        AND n.nspname<>'information_schema'
        AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_depend d
          WHERE d.classid='pg_catalog.pg_class'::regclass AND d.objid=c.oid AND d.deptype='e'
            AND d.refclassid='pg_catalog.pg_extension'::regclass)
        AND NOT(n.nspname IN ('_timescaledb_internal','_timescaledb_catalog','_timescaledb_config','_timescaledb_cache')
          AND EXISTS(SELECT 1 FROM pg_catalog.pg_extension WHERE extname='timescaledb'))
      ORDER BY n.nspname,c.relname LIMIT :limit""")
    # Exclude only exact owned control names; a similarly prefixed user table
    # still participates and must appear in the external universe plan.
    rows = db.execute(query, {"limit": MAX_TABLES + len(OWN_TABLES) + 1}).mappings().all()
    if len(rows) > MAX_TABLES + len(OWN_TABLES):
        raise ArchiveBlocked("Restore relation inventory exceeds metadata budget")
    return {
        (row["schema"], row["name"])
        for row in rows
        if not (row["schema"] == "public" and row["name"] in OWN_TABLES)
    }


def bind(db, plan):
    validate(plan)
    expected = {("public", name) for name in plan["universe"]["tables"]}
    if relation_inventory(db) != expected:
        raise ArchiveBlocked("Unknown or missing application relation in restore universe")
    table = Table(
        "greenmind_restore_universe",
        MetaData(),
        Column("id", Integer, primary_key=True),
        Column("sha256", String(64), nullable=False),
    )
    table.create(db, checkfirst=True)
    values = db.execute(select(table).limit(2)).mappings().all()
    pin = digest(
        {
            "universe": plan["universe"],
            **{key: plan[key] for key in ("environment", "source", "schema_master_sha256")},
        }
    )
    if values:
        if len(values) != 1 or values[0]["id"] != 1 or values[0]["sha256"] != pin:
            raise ArchiveBlocked("Restore application universe changed")
    else:
        db.execute(table.insert().values(id=1, sha256=pin))
    return pin


def tables(db, plan):
    metadata = MetaData()
    values = {
        name: Table(name, metadata, autoload_with=db) for name in sorted(plan["universe"]["tables"])
    }
    masters = {
        name: table
        for name, table in values.items()
        if plan["universe"]["tables"][name] == "master"
    }
    return values, masters
