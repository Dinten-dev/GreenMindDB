"""Transactional mutation evidence for an operator-owned isolated restore DB.

The database owner/admin is trusted. Normal writes cannot bypass ALWAYS triggers
or an origin check. No product SQLite fallback, source access or application-row
scan is provided; tests inject their own small physical SQLite adapter explicitly.
"""

import re

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    Integer,
    MetaData,
    String,
    Table,
    select,
    text,
)

from app.raw_archive.policy import ArchiveBlocked

from . import restore_origin_capability as origin_capability
from .codec import digest

STATE = "greenmind_restore_fence"
REGISTRY = "greenmind_restore_fence_table"
FUNCTION = "greenmind_restore_track_v1"
ROW_TRIGGER = "greenmind_restore_row_v1"
TRUNCATE_TRIGGER = "greenmind_restore_truncate_v1"
FORMAT = "isolated-restore-mutation-fence-v2"
ALWAYS = "pg-always-v1"
_SHA = re.compile(r"[a-f0-9]{64}")
_NAME = re.compile(r"[a-z][a-z0-9_]{0,62}")


def _require(value, reason):
    if not value:
        raise ArchiveBlocked(reason)


def _postgres(db):
    _require(db.dialect.name == "postgresql", "Restore mutation fence requires PostgreSQL")
    _require(
        db.execute(text("SELECT current_setting('session_replication_role')")).scalar_one()
        == "origin",
        "Restore mutation fence requires origin replication role",
    )


def _identity(db):
    return dict(
        db.execute(
            text("""SELECT current_database() AS name,
      (SELECT oid::bigint FROM pg_catalog.pg_database WHERE datname=current_database()) AS oid,
      inet_server_addr()::text AS address, inet_server_port() AS port""")
        )
        .mappings()
        .one()
    )


def _control_tables():
    metadata = MetaData(schema="public")
    state = Table(
        STATE,
        metadata,
        Column("id", Integer, primary_key=True),
        Column("format", String(64), nullable=False),
        Column("mode", String(64), nullable=False),
        Column("capability_sha256", String(64)),
        Column("target_sha256", String(64), nullable=False),
        Column("domain_sha256", String(64), nullable=False),
        Column("domain", JSON, nullable=False),
        Column("control_sha256", String(64), nullable=False),
        Column("registry_sha256", String(64), nullable=False),
        Column("epoch", BigInteger, nullable=False),
        Column("expected_epoch", BigInteger, nullable=False),
        Column("master_epoch", BigInteger, nullable=False),
        Column("phase", String(16), nullable=False),
        Column("audit_epoch", BigInteger),
    )
    registry = Table(
        REGISTRY,
        metadata,
        Column("table_name", String(63), primary_key=True),
        Column("master", Boolean, nullable=False),
        Column("rows", BigInteger, nullable=False),
        Column("expected_rows", BigInteger, nullable=False),
        Column("relation_sha256", String(64), nullable=False),
        Column("coverage", JSON, nullable=False),
    )
    return state, registry


def _control_presence(db):
    row = (
        db.execute(
            text("""SELECT
      pg_catalog.to_regclass('public.greenmind_restore_fence') IS NOT NULL AS state,
      pg_catalog.to_regclass('public.greenmind_restore_fence_table') IS NOT NULL AS registry,
      pg_catalog.to_regprocedure('public.greenmind_restore_track_v1()') IS NOT NULL AS function
    """)
        )
        .mappings()
        .one()
    )
    return tuple(row[key] for key in ("state", "registry", "function"))


def _relation_pin(db, name, schema="public"):
    # Stable catalog fields, not search_path-sensitive rendered constraint SQL.
    value = db.execute(
        text("""SELECT jsonb_build_object(
      'relation',to_jsonb(c)-'relpages'-'reltuples'-'relallvisible'-'relfrozenxid'-'relminmxid'-'relhassubclass',
      'columns',(SELECT jsonb_agg(to_jsonb(a) ORDER BY a.attnum)
        FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0),
      'constraints',(SELECT jsonb_agg(to_jsonb(x) ORDER BY x.oid)
        FROM pg_catalog.pg_constraint x WHERE x.conrelid=c.oid),
      'indexes',(SELECT jsonb_agg(to_jsonb(i) ORDER BY i.indexrelid)
        FROM pg_catalog.pg_index i WHERE i.indrelid=c.oid),
      'triggers',(SELECT jsonb_agg(jsonb_build_object('trigger',to_jsonb(t),
        'function',to_jsonb(p)) ORDER BY t.oid) FROM pg_catalog.pg_trigger t
        JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid WHERE t.tgrelid=c.oid))
      FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname=:schema AND c.relname=:name AND c.relkind='r'
        AND c.relpersistence='p'"""),
        {"name": name, "schema": schema},
    ).scalar_one_or_none()
    _require(value is not None, "Restore fence requires exact permanent table identity")
    return digest(value)


def _function_pin(db):
    value = db.execute(
        text("""SELECT to_jsonb(p) FROM pg_catalog.pg_proc p
      JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
      WHERE n.nspname='public' AND p.proname='greenmind_restore_track_v1'
        AND p.pronargs=0 AND p.prorettype='pg_catalog.trigger'::pg_catalog.regtype""")
    ).scalar_one_or_none()
    _require(value is not None, "Restore mutation function identity missing")
    return digest(value)


def _control_pin(db):
    return digest([_relation_pin(db, STATE), _relation_pin(db, REGISTRY), _function_pin(db)])


def _install_function(db):
    db.execute(
        text("""CREATE FUNCTION public.greenmind_restore_track_v1()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $fence$
    DECLARE mode text; is_master boolean; logical_name text; parent_oid oid;
    BEGIN
      IF current_setting('session_replication_role') <> 'origin' OR TG_NARGS<>1 THEN
        RAISE EXCEPTION 'restore_fence_origin_required';
      END IF;
      logical_name := TG_ARGV[0];
      parent_oid := to_regclass(format('public.%I',logical_name));
      IF parent_oid IS NULL OR (TG_RELID<>parent_oid AND NOT EXISTS (
        WITH RECURSIVE ancestors(relid,depth) AS (
          SELECT inhparent,1 FROM pg_catalog.pg_inherits WHERE inhrelid=TG_RELID
          UNION ALL SELECT i.inhparent,a.depth+1 FROM pg_catalog.pg_inherits i
            JOIN ancestors a ON i.inhrelid=a.relid WHERE a.depth<8)
        SELECT 1 FROM ancestors WHERE relid=parent_oid)) THEN
        RAISE EXCEPTION 'restore_fence_parent_required';
      END IF;
      SELECT phase INTO mode FROM public.greenmind_restore_fence WHERE id=1 FOR UPDATE;
      IF mode IS DISTINCT FROM 'loading' THEN
        RAISE EXCEPTION 'restore_fence_frozen';
      END IF;
      SELECT master INTO is_master FROM public.greenmind_restore_fence_table
        WHERE table_name=logical_name;
      IF NOT FOUND THEN RAISE EXCEPTION 'restore_fence_registration_required'; END IF;
      UPDATE public.greenmind_restore_fence SET epoch=epoch+1,
        master_epoch=master_epoch+CASE WHEN is_master THEN 1 ELSE 0 END WHERE id=1;
      IF NOT is_master THEN
        IF TG_OP='TRUNCATE' THEN
          UPDATE public.greenmind_restore_fence_table SET "rows"=0 WHERE table_name=logical_name;
        ELSIF TG_OP='INSERT' THEN
          UPDATE public.greenmind_restore_fence_table SET "rows"="rows"+1 WHERE table_name=logical_name;
        ELSIF TG_OP='DELETE' THEN
          UPDATE public.greenmind_restore_fence_table SET "rows"="rows"-1 WHERE table_name=logical_name;
        END IF;
      END IF;
      RETURN NULL;
    END $fence$""")
    )


def _quote(name):
    _require(
        isinstance(name, str) and 0 < len(name) <= 63 and "\x00" not in name,
        "Exact restore identifier required",
    )
    return '"' + name.replace('"', '""') + '"'


def _trigger(db, name, schema, trigger):
    return (
        db.execute(
            text("""SELECT t.tgenabled AS enabled,t.tgtype AS kind,
      t.tgfoid='public.greenmind_restore_track_v1()'::pg_catalog.regprocedure AS function,
      t.tgnargs AS nargs,t.tgargs AS args,t.tgqual IS NULL AS unconditional
      FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
      JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname=:schema AND c.relname=:name AND t.tgname=:trigger"""),
            {"schema": schema, "name": name, "trigger": trigger},
        )
        .mappings()
        .one_or_none()
    )


def _valid_trigger(row, logical, kind, *, allow_origin=False, mode=ALWAYS):
    enabled = {"O"} if mode == origin_capability.MODE else ({"A", "O"} if allow_origin else {"A"})
    return (
        row is not None
        and row["enabled"] in enabled
        and row["kind"] == kind
        and row["function"] is True
        and row["nargs"] == 1
        and bytes(row["args"]) == logical.encode() + b"\x00"
        and row["unconditional"] is True
    )


def _install_triggers(db, name, logical=None, schema="public", *, new_chunk=False, mode=ALWAYS):
    logical = logical or name
    _require(_NAME.fullmatch(logical) is not None, "Exact restore table name required")
    qualified = _quote(schema) + "." + _quote(name)
    for trigger, event in (
        (ROW_TRIGGER, "INSERT OR UPDATE OR DELETE FOR EACH ROW"),
        (TRUNCATE_TRIGGER, "TRUNCATE FOR EACH STATEMENT"),
    ):
        existing = _trigger(db, name, schema, trigger)
        kind = 29 if trigger == ROW_TRIGGER else 32
        if existing is None:
            _require(
                not new_chunk or trigger == TRUNCATE_TRIGGER,
                "New restore child lacks inherited row fence",
            )
            db.execute(
                text(
                    f'CREATE TRIGGER "{trigger}" AFTER {event.split(" FOR EACH ")[0]} ON {qualified} '
                    f"FOR EACH {event.split(' FOR EACH ')[1]} EXECUTE FUNCTION public.{FUNCTION}('{logical}')"
                )
            )
        else:
            _require(
                _valid_trigger(existing, logical, kind, allow_origin=True, mode=mode),
                "Existing restore trigger differs",
            )
        if mode == ALWAYS:
            db.execute(text(f'ALTER TABLE {qualified} ENABLE ALWAYS TRIGGER "{trigger}"'))


# SQL xmin exposes raw 32-bit IDs even for frozen tuples. Do not infer an
# epoch from that raw value: after wrap an old frozen row could alias an active
# transaction. Only the first, demonstrably unwrapped epoch is admitted here.
# Within that epoch, normal MVCC visibility + an in-progress inserting XID
# proves an own live/subcommitted subtransaction (other live rows are invisible).
# Unknown statuses and every wrap boundary remain closed pending another proof.
_OWN_INSERTION = """CASE
      WHEN authority.top_id >= 3 AND authority.top_id < 4294967296
       AND c.xmin::text::numeric >= authority.top_id
      THEN (pg_catalog.pg_xact_status(c.xmin::text::pg_catalog.xid8) = 'in progress') IS TRUE
      ELSE false END"""


def _descendants(db, name):
    values = (
        db.execute(
            text(
                """WITH RECURSIVE authority AS MATERIALIZED (
      SELECT pg_catalog.pg_current_xact_id()::text::numeric AS top_id
      ), children(relid,depth) AS (
      SELECT inhrelid,1 FROM pg_catalog.pg_inherits
        WHERE inhparent=pg_catalog.to_regclass(format('public.%I',:name))
      UNION ALL SELECT i.inhrelid,c.depth+1 FROM pg_catalog.pg_inherits i
        JOIN children c ON i.inhparent=c.relid WHERE c.depth<8)
      SELECT c.oid::bigint AS oid,c.relname AS name,n.nspname AS schema,c.relkind AS kind,
        children.depth,"""
                + _OWN_INSERTION
                + """ AS own_transaction,
        EXISTS(SELECT 1 FROM pg_catalog.pg_inherits i WHERE i.inhparent=c.oid) AS has_children
      FROM children JOIN pg_catalog.pg_class c ON c.oid=children.relid
      JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
      CROSS JOIN authority LIMIT 257"""
            ),
            {"name": name},
        )
        .mappings()
        .all()
    )
    _require(
        len(values) <= 256
        and len({r["oid"] for r in values}) == len(values)
        and all(r["kind"] == "r" and (r["depth"] < 8 or not r["has_children"]) for r in values),
        "Restore physical child coverage exceeds supported bounds",
    )
    return sorted(values, key=lambda item: item["oid"])


def _coverage(db, name, known, *, install=False, prepare_new=False, mode=ALWAYS):
    result = {}
    for child in _descendants(db, name):
        key = str(child["oid"])
        if install:
            _install_triggers(db, child["name"], name, child["schema"], mode=mode)
        elif prepare_new and key not in known:
            _require(
                child["own_transaction"] is True, "Unbound external restore child cannot be adopted"
            )
            # New TS chunks clone the original row trigger, initially in origin
            # mode. Promote it and install a statement fence before this same
            # batch/chunk transaction can commit; never repair an old child.
            _install_triggers(db, child["name"], name, child["schema"], new_chunk=True, mode=mode)
        for trigger, kind in ((ROW_TRIGGER, 29), (TRUNCATE_TRIGGER, 32)):
            _require(
                _valid_trigger(
                    _trigger(db, child["name"], child["schema"], trigger), name, kind, mode=mode
                ),
                "Restore physical child trigger coverage changed",
            )
        result[key] = _relation_pin(db, child["name"], child["schema"])
    return result


def _registrations(db, registry):
    rows = db.execute(select(registry).order_by(registry.c.table_name).limit(65)).mappings().all()
    _require(len(rows) <= 64, "Restore fence registration budget exceeded")
    return [dict(row) for row in rows]


def _registry_pin(rows):
    return digest(
        [(r["table_name"], r["master"], r["relation_sha256"], r["coverage"]) for r in rows]
    )


def _snapshot(state, rows):
    return {
        "epoch": state["epoch"],
        "master_epoch": state["master_epoch"],
        "phase": state["phase"],
        "audit_epoch": state["audit_epoch"],
        "rows": {r["table_name"]: r["rows"] for r in rows if not r["master"]},
    }


def _load(db, *, prepare_new=False):
    _postgres(db)
    _require(_control_presence(db) == (True, True, True), "Restore mutation fence missing")
    state_table, registry = _control_tables()
    versions = db.execute(select(state_table.c.format).limit(2)).scalars().all()
    _require(versions == [FORMAT], "Restore fence version is not compatible; no automatic upgrade")
    values = db.execute(select(state_table).limit(2).with_for_update()).mappings().all()
    _require(len(values) == 1 and values[0]["id"] == 1, "Exact restore fence state required")
    state = dict(values[0])
    _require(
        digest(state["domain"]) == state["domain_sha256"]
        and _domain_mode(state["domain"]) == (state["mode"], state["capability_sha256"]),
        "Restore fence mode/domain binding changed",
    )
    _require(
        state["mode"] in {ALWAYS, origin_capability.MODE}, "Unknown explicit restore fence mode"
    )
    if state["mode"] == origin_capability.MODE:
        _require(
            isinstance(state["capability_sha256"], str)
            and _SHA.fullmatch(state["capability_sha256"]),
            "Pinned origin capability required",
        )
        origin_capability.verify(db, state["capability_sha256"])
    else:
        _require(
            state["capability_sha256"] is None, "ALWAYS fence cannot adopt an origin capability"
        )
    identity = _identity(db)
    _require(
        "_restore_" in identity["name"]
        and digest(identity) == state["target_sha256"]
        and state["format"] == FORMAT
        and _control_pin(db) == state["control_sha256"],
        "Restore fence target or control catalog changed",
    )
    rows = _registrations(db, registry)
    _require(_registry_pin(rows) == state["registry_sha256"], "Restore fence registry changed")
    for row in rows:
        _require(
            _relation_pin(db, row["table_name"]) == row["relation_sha256"],
            "Restore relation or trigger catalog changed",
        )
        coverage = _coverage(
            db,
            row["table_name"],
            row["coverage"],
            prepare_new=prepare_new and state["phase"] == "loading",
            mode=state["mode"],
        )
        _require(
            all(coverage.get(k) == v for k, v in row["coverage"].items())
            and (prepare_new or coverage == row["coverage"]),
            "Restore physical child identity changed",
        )
        row["actual_coverage"] = coverage
    return state_table, registry, state, rows


def _expected(state, rows):
    _require(
        all(
            type(state[key]) is int and state[key] >= 0
            for key in ("epoch", "expected_epoch", "master_epoch")
        )
        and state["phase"] in {"loading", "auditing"}
        and (
            (state["phase"] == "loading" and state["audit_epoch"] is None)
            or (state["phase"] == "auditing" and state["audit_epoch"] == state["epoch"])
        )
        and state["epoch"] == state["expected_epoch"]
        and all(r["rows"] == r["expected_rows"] and r["rows"] >= 0 for r in rows),
        "Unexpected restore database mutation",
    )


def _domain_mode(domain):
    keys = {"environment", "source", "schema_master_sha256"}
    _require(
        isinstance(domain, dict)
        and set(domain) in (keys, keys | {"fence_mode", "fence_capability_sha256"}),
        "Exact restore fence domain required",
    )
    mode = domain.get("fence_mode", ALWAYS)
    capability = domain.get("fence_capability_sha256")
    _require(mode in {ALWAYS, origin_capability.MODE}, "Unknown explicit restore fence mode")
    if mode == origin_capability.MODE:
        _require(
            "fence_mode" in domain and isinstance(capability, str) and _SHA.fullmatch(capability),
            "Explicit pinned origin capability required",
        )
    else:
        _require(capability is None, "ALWAYS fence cannot adopt an origin capability")
    return mode, capability


def ensure(db, tables, master_names, target_sha256, domain):
    """Register only new, empty data tables; preserve existing master contents."""
    _postgres(db)
    mode, capability = _domain_mode(domain)
    if mode == origin_capability.MODE:
        origin_capability.verify(db, capability)
    _require(
        isinstance(tables, dict)
        and 0 < len(tables) <= 64
        and isinstance(master_names, set)
        and master_names <= tables.keys()
        and isinstance(target_sha256, str)
        and _SHA.fullmatch(target_sha256)
        and isinstance(domain, dict)
        and domain["environment"] in {"production", "staging"}
        and domain["source"] in {"gateway", "direct"}
        and isinstance(domain["schema_master_sha256"], str)
        and _SHA.fullmatch(domain["schema_master_sha256"]),
        "Exact restore fence scope required",
    )
    for name, table in tables.items():
        _require(
            isinstance(name, str)
            and _NAME.fullmatch(name)
            and name == table.name
            and table.schema in {None, "public"}
            and name not in {STATE, REGISTRY},
            "Exact public restore table required",
        )
    identity = _identity(db)
    _require(
        "_restore_" in identity["name"] and digest(identity) == target_sha256,
        "Isolated restore target identity required",
    )
    presence = _control_presence(db)
    state_table, registry = _control_tables()
    if presence == (False, False, False):
        state_table.metadata.create_all(db)
        _install_function(db)
        db.execute(
            state_table.insert().values(
                id=1,
                format=FORMAT,
                mode=mode,
                capability_sha256=capability,
                target_sha256=target_sha256,
                domain_sha256=digest(domain),
                domain=domain,
                control_sha256=_control_pin(db),
                registry_sha256=digest([]),
                epoch=0,
                expected_epoch=0,
                master_epoch=0,
                phase="loading",
                audit_epoch=None,
            )
        )
    else:
        _require(presence == (True, True, True), "Partial restore fence cannot be adopted")
    _, _, state, rows = _load(db)
    _expected(state, rows)
    _require(
        state["phase"] == "loading" and state["domain_sha256"] == digest(domain),
        "Restore fence domain or phase changed",
    )
    known = {row["table_name"]: row for row in rows}
    for name, table in sorted(tables.items()):
        if name in known:
            _require(known[name]["master"] == (name in master_names), "Restore table role changed")
            continue
        _require(len(known) < 64, "Restore fence registration budget exceeded")
        _relation_pin(db, name)
        if name not in master_names:
            _require(
                db.execute(select(1).select_from(table).limit(1)).first() is None,
                "New restore data table must be empty",
            )
        _install_triggers(db, name, mode=mode)
        db.execute(
            registry.insert().values(
                table_name=name,
                master=name in master_names,
                rows=0,
                expected_rows=0,
                relation_sha256=_relation_pin(db, name),
                coverage=_coverage(db, name, {}, install=True, mode=mode),
            )
        )
        known[name] = {"master": name in master_names}
    rows = _registrations(db, registry)
    db.execute(
        state_table.update()
        .where(state_table.c.id == 1)
        .values(registry_sha256=_registry_pin(rows))
    )
    return check(db)


def check(db):
    _, _, state, rows = _load(db)
    _expected(state, rows)
    return _snapshot(state, rows)


def accept(db, before, inserted):
    state_table, registry, state, rows = _load(db, prepare_new=True)
    expected_rows = {row["table_name"]: row["expected_rows"] for row in rows if not row["master"]}
    _require(
        isinstance(inserted, dict)
        and inserted.keys() <= expected_rows.keys()
        and all(type(v) is int and 0 <= v < 2**63 for v in inserted.values())
        and isinstance(before, dict)
        and set(before) == {"epoch", "master_epoch", "phase", "audit_epoch", "rows"}
        and before["phase"] == state["phase"] == "loading"
        and before["audit_epoch"] is None
        and before["epoch"] == state["expected_epoch"]
        and before["rows"] == expected_rows
        and before["master_epoch"] == state["master_epoch"]
        and state["epoch"] - before["epoch"] == sum(inserted.values())
        and all(
            row["rows"] == row["expected_rows"] + inserted.get(row["table_name"], 0)
            for row in rows
            if not row["master"]
        ),
        "Restore batch contains unexpected mutations",
    )
    for row in rows:
        values = {"coverage": row["actual_coverage"]}
        if not row["master"]:
            values["expected_rows"] = row["rows"]
        db.execute(
            registry.update().where(registry.c.table_name == row["table_name"]).values(**values)
        )
    updated = _registrations(db, registry)
    db.execute(
        state_table.update()
        .where(state_table.c.id == 1)
        .values(expected_epoch=state["epoch"], registry_sha256=_registry_pin(updated))
    )
    return check(db)


def freeze(db):
    state_table, _, state, _ = _load(db)
    result = check(db)
    _require(state["phase"] in {"loading", "auditing"}, "Unknown restore fence phase")
    if state["phase"] == "loading":
        db.execute(
            state_table.update()
            .where(state_table.c.id == 1)
            .values(phase="auditing", audit_epoch=state["epoch"])
        )
    return check_audit(db, result["epoch"])


def check_audit(db, epoch):
    result = check(db)
    _require(
        type(epoch) is int
        and result["phase"] == "auditing"
        and result["audit_epoch"] == result["epoch"] == epoch,
        "Restore audit mutation fence changed",
    )
    return result
