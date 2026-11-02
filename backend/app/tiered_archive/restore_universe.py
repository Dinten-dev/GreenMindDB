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
        "greenmind_science_restore",
        "greenmind_science_restore_part",
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
        or set(value)
        != (
            {"version", "tables"}
            if value.get("version") == 1
            else {"version", "tables", "empty_compressed_storage"}
        )
        or type(value["version"]) is not int
        or value["version"] not in {1, 2}
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

    if value["version"] == 2:
        from .restore_compressed import validate as validate_compressed

        validate_compressed(value["empty_compressed_storage"], value["tables"])


def chunk_exemption(db):
    info = (
        db.execute(
            text("""SELECT e.oid,e.extversion,
      (SELECT count(*) FROM pg_catalog.pg_depend d JOIN pg_catalog.pg_class c ON c.oid=d.objid
       JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
       WHERE d.classid='pg_catalog.pg_class'::regclass AND d.deptype='e'
         AND d.refclassid='pg_catalog.pg_extension'::regclass AND d.refobjid=e.oid
         AND n.nspname='_timescaledb_catalog' AND c.relname IN ('chunk','hypertable')) AS catalogs
      FROM pg_catalog.pg_extension e WHERE e.extname='timescaledb'""")
        )
        .mappings()
        .one_or_none()
    )
    if info is None:
        return ""
    if info["extversion"] != "2.17.2" or info["catalogs"] != 2:
        raise ArchiveBlocked("Timescale chunk catalog provenance/version is unproved")
    # Only physical chunks of a PUBLIC logical hypertable are covered by the
    # generic inheritance fence. Compressed/private-root storage is NOT silently
    # exempted; it stays visible and blocks until explicit coverage is proved.
    return """ AND NOT EXISTS(SELECT 1 FROM _timescaledb_catalog.chunk ch
      JOIN _timescaledb_catalog.hypertable h ON h.id=ch.hypertable_id
      JOIN pg_catalog.pg_inherits inh ON inh.inhrelid=c.oid
      JOIN pg_catalog.pg_class parent ON parent.oid=inh.inhparent
      JOIN pg_catalog.pg_namespace pn ON pn.oid=parent.relnamespace
      WHERE ch.schema_name=n.nspname AND ch.table_name=c.relname AND NOT ch.dropped
        AND h.schema_name='public' AND pn.nspname=h.schema_name AND parent.relname=h.table_name) """


def relation_inventory(db, empty_compressed_oids=()):
    if db.dialect.name != "postgresql":
        raise ArchiveBlocked("Native restore-universe catalog requires PostgreSQL")
    from app.raw_archive.scientific_restore import validate_application_journals

    validate_application_journals(db)
    # A schema name is never proof of extension ownership. Retain unrelated
    # internal-schema user tables in the result so the exact universe rejects them.
    query = text(
        """SELECT n.nspname AS schema,c.relname AS name FROM pg_catalog.pg_class c
      JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
      WHERE c.relkind IN ('r','p','m','f') AND left(n.nspname,3)<>'pg_'
        AND n.nspname<>'information_schema'
        AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_depend d
          WHERE d.classid='pg_catalog.pg_class'::regclass AND d.objid=c.oid AND d.deptype='e'
            AND d.refclassid='pg_catalog.pg_extension'::regclass)"""
        + chunk_exemption(db)
        + (
            " AND c.oid::bigint <> ALL(CAST(:empty_oids AS bigint[]))"
            if empty_compressed_oids
            else ""
        )
        + " ORDER BY n.nspname,c.relname LIMIT :limit"
    )
    params = {"limit": MAX_TABLES + len(OWN_TABLES) + 1}
    if empty_compressed_oids:
        params["empty_oids"] = list(empty_compressed_oids)
    rows = db.execute(query, params).mappings().all()
    if len(rows) > MAX_TABLES + len(OWN_TABLES):
        raise ArchiveBlocked("Restore relation inventory exceeds metadata budget")
    return {
        (row["schema"], row["name"])
        for row in rows
        if not (row["schema"] == "public" and row["name"] in OWN_TABLES)
    }


def domain(plan):
    result = {key: plan[key] for key in ("environment", "source", "schema_master_sha256")}
    fields = {"fence_mode", "fence_capability_sha256"}
    if fields.intersection(plan):
        import re

        if (
            not fields <= plan.keys()
            or plan["fence_mode"] != "timescale-origin-guarded-v1"
            or not isinstance(plan["fence_capability_sha256"], str)
            or re.fullmatch("[a-f0-9]{64}", plan["fence_capability_sha256"]) is None
        ):
            raise ArchiveBlocked("Explicit complete origin domain required")
        result.update({key: plan[key] for key in fields})
    return result


def bind(db, plan):
    validate(plan)
    expected = {("public", name) for name in plan["universe"]["tables"]}
    from .restore_compressed import check as check_compressed

    empty_oids = check_compressed(db, plan)
    actual = relation_inventory(db, empty_oids) if empty_oids else relation_inventory(db)
    if actual != expected:
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
            **domain(plan),
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
