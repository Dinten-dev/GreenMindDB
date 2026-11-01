"""Native append-only Source audit for SQL retention; no DDL is executed here.

The SQL writer identity is trusted to issue audit INSERTs only through the
pinned application code. Database owner/superuser remains a separate trust
boundary. Every deletion and its audit commit in the same Source transaction.
"""

import hashlib
import json
import re

from sqlalchemy import text

from app.raw_archive.policy import ArchiveBlocked

from . import codec, restore_fence

TABLE = "greenmind_sql_deletion_audit_v1"
FUNCTION = "greenmind_sql_audit_immutable_v1"
ROW_TABLE = "greenmind_sql_deletion_row_audit_v1"
ROW_FUNCTION = "greenmind_sql_capture_deleted_row_v1"
ROW_BODY = """
DECLARE audit_event text;
BEGIN
  IF TG_RELID <> pg_catalog.to_regclass('public.ingest_log') OR TG_OP <> 'DELETE' OR TG_LEVEL <> 'ROW' OR TG_WHEN <> 'AFTER' OR TG_NARGS <> 0 THEN
    RAISE EXCEPTION 'invalid_sql_audit_trigger_scope';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(5137854358536410436::bigint);
  audit_event := pg_catalog.current_setting('greenmind.sql_audit_event', true);
  IF audit_event IS NULL OR audit_event = '' THEN audit_event := pg_catalog.repeat('0',64); END IF;
  IF audit_event !~ '^[a-f0-9]{64}$' THEN RAISE EXCEPTION 'invalid_sql_audit_event'; END IF;
  INSERT INTO public.greenmind_sql_deletion_row_audit_v1(event_id,transaction_id,row_sha256)
    VALUES(audit_event,pg_catalog.txid_current(),pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(pg_catalog.row_to_json(OLD)::text,'UTF8')),'hex'));
  RETURN OLD;
END
"""
BODY = "\nBEGIN\n  RAISE EXCEPTION 'immutable_sql_deletion_audit';\nEND\n"
GLOBAL_LOCK = 0x474D53514C415544
HEX = re.compile(r"[a-f0-9]{64}")
EPOCH = re.compile(r"[a-f0-9]{32}")


def need(value, message):
    if not value:
        raise ArchiveBlocked(message)


def ddl_plan(writer_role):
    """Return additive REVIEW_ONLY SQL; caller never executes this plan."""
    need(
        type(writer_role) is str and re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", writer_role),
        "Exact SQL writer role required",
    )
    ddl = f'''BEGIN;
SET LOCAL lock_timeout='150ms';
SET LOCAL statement_timeout='8s';
CREATE TABLE public.{TABLE} (
 event_id text PRIMARY KEY CHECK (event_id ~ '^[a-f0-9]{{64}}$'),
 epoch text NOT NULL CHECK (epoch ~ '^[a-f0-9]{{32}}$'),
 kind text NOT NULL CHECK (kind IN ('genesis','commit')),
 body text NOT NULL CHECK (pg_catalog.octet_length(body) <= 8192),
 transaction_id bigint NOT NULL DEFAULT pg_catalog.txid_current(),
 created_at timestamptz NOT NULL DEFAULT pg_catalog.clock_timestamp()
);
CREATE TABLE public.{ROW_TABLE} (
 event_id text NOT NULL CHECK (event_id ~ '^[a-f0-9]{{64}}$'),
 transaction_id bigint NOT NULL,
 row_sha256 text NOT NULL CHECK (row_sha256 ~ '^[a-f0-9]{{64}}$'),
 PRIMARY KEY (event_id,transaction_id,row_sha256)
);
CREATE FUNCTION public.{ROW_FUNCTION}() RETURNS trigger LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $auditrow${ROW_BODY}$auditrow$;
CREATE TRIGGER gm_sql_capture_deleted_row AFTER DELETE ON public.ingest_log FOR EACH ROW EXECUTE FUNCTION public.{ROW_FUNCTION}();
ALTER TABLE public.ingest_log ENABLE ALWAYS TRIGGER gm_sql_capture_deleted_row;
CREATE FUNCTION public.{FUNCTION}() RETURNS trigger LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path=pg_catalog AS $audit${BODY}$audit$;
CREATE TRIGGER gm_sql_audit_immutable_row BEFORE UPDATE OR DELETE ON public.{TABLE} FOR EACH ROW EXECUTE FUNCTION public.{FUNCTION}();
CREATE TRIGGER gm_sql_audit_immutable_truncate BEFORE TRUNCATE ON public.{TABLE} FOR EACH STATEMENT EXECUTE FUNCTION public.{FUNCTION}();
ALTER TABLE public.{TABLE} ENABLE ALWAYS TRIGGER gm_sql_audit_immutable_row;
ALTER TABLE public.{TABLE} ENABLE ALWAYS TRIGGER gm_sql_audit_immutable_truncate;
CREATE TRIGGER gm_sql_audit_immutable_row BEFORE UPDATE OR DELETE ON public.{ROW_TABLE} FOR EACH ROW EXECUTE FUNCTION public.{FUNCTION}();
CREATE TRIGGER gm_sql_audit_immutable_truncate BEFORE TRUNCATE ON public.{ROW_TABLE} FOR EACH STATEMENT EXECUTE FUNCTION public.{FUNCTION}();
ALTER TABLE public.{ROW_TABLE} ENABLE ALWAYS TRIGGER gm_sql_audit_immutable_row;
ALTER TABLE public.{ROW_TABLE} ENABLE ALWAYS TRIGGER gm_sql_audit_immutable_truncate;
REVOKE ALL ON public.{TABLE} FROM PUBLIC;
REVOKE ALL ON public.{ROW_TABLE} FROM PUBLIC;
GRANT SELECT,INSERT ON public.{TABLE} TO "{writer_role}";
GRANT SELECT ON public.{ROW_TABLE} TO "{writer_role}";
COMMIT;'''
    return {
        "status": "REVIEW_ONLY",
        "format": "greenmind.sql-source-audit-ddl.v1",
        "writer_role": writer_role,
        "ddl": ddl,
        "ddl_sha256": hashlib.sha256(ddl.encode()).hexdigest(),
        "executed": False,
    }


def identity_anchor(identity):
    """Stable DB/role identity; the live guard separately pins each postmaster generation."""
    keys = (
        "database",
        "database_oid",
        "role",
        "role_oid",
        "server_address",
        "server_port",
        "version",
    )
    need(
        type(identity) is dict
        and all(key in identity for key in keys)
        and all(
            type(identity[key]) is str and identity[key]
            for key in ("database", "role", "server_address")
        )
        and all(
            type(identity[key]) is int and identity[key] > 0
            for key in ("database_oid", "role_oid", "server_port", "version")
        ),
        "Stable Source database identity required",
    )
    return codec.digest({key: identity[key] for key in keys})


def inspect(db, expected_schema_sha256, expected_identity):
    """Check actual PostgreSQL relation, trigger code/privileges and source role."""
    from .sql_deletion_guard import _identity

    need(
        db.dialect.name == "postgresql"
        and type(expected_identity) is dict
        and HEX.fullmatch(expected_schema_sha256 or ""),
        "Native Source audit pins required",
    )
    identity = _identity(db)
    need(
        identity == expected_identity
        and identity.get("restricted_role") is True
        and identity.get("replication_role") == "origin"
        and identity.get("read_only") == "off"
        and identity.get("role") == identity.get("session_role"),
        "Source SQL audit identity differs",
    )
    columns = db.execute(
        text("""SELECT a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod) AS type,a.attnotnull
        FROM pg_catalog.pg_attribute a WHERE a.attrelid=pg_catalog.to_regclass(:table)
        AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum"""),
        {"table": "public." + TABLE},
    ).all()
    need(
        [tuple(row) for row in columns]
        == [
            ("event_id", "text", True),
            ("epoch", "text", True),
            ("kind", "text", True),
            ("body", "text", True),
            ("transaction_id", "bigint", True),
            ("created_at", "timestamp with time zone", True),
        ],
        "Source SQL audit columns differ",
    )
    props = (
        db.execute(
            text("""SELECT c.relkind,c.relpersistence,c.relrowsecurity,c.relforcerowsecurity,
        pg_catalog.pg_has_role((SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user),c.relowner,'MEMBER') AS owner_member,
        pg_catalog.has_table_privilege(current_user,c.oid,'SELECT') AS can_select,
        pg_catalog.has_table_privilege(current_user,c.oid,'INSERT') AS can_insert,
        pg_catalog.has_table_privilege(current_user,c.oid,'UPDATE,DELETE,TRUNCATE,TRIGGER') AS can_mutate,
        EXISTS(SELECT 1 FROM pg_catalog.pg_inherits WHERE inhparent=c.oid OR inhrelid=c.oid) AS inherited,
        EXISTS(SELECT 1 FROM pg_catalog.pg_index i WHERE i.indrelid=c.oid AND i.indisprimary AND i.indisvalid AND i.indisready AND i.indnkeyatts=1 AND i.indkey[0]=1 AND i.indpred IS NULL) AS exact_primary_key
        FROM pg_catalog.pg_class c WHERE c.oid=pg_catalog.to_regclass(:table)"""),
            {"table": "public." + TABLE},
        )
        .mappings()
        .one()
    )
    need(
        dict(props)
        == {
            "relkind": "r",
            "relpersistence": "p",
            "relrowsecurity": False,
            "relforcerowsecurity": False,
            "owner_member": False,
            "can_select": True,
            "can_insert": True,
            "can_mutate": False,
            "inherited": False,
            "exact_primary_key": True,
        },
        "Source SQL audit ownership or privileges differ",
    )
    proc = (
        db.execute(
            text("""SELECT p.prosrc AS body,p.prosecdef AS security_definer,p.provolatile AS volatility,p.proconfig AS config,
        (SELECT lanname FROM pg_catalog.pg_language WHERE oid=p.prolang) AS language,
        pg_catalog.pg_has_role((SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user),p.proowner,'MEMBER') AS owner_member
        FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname=:function AND p.pronargs=0 AND p.prorettype='pg_catalog.trigger'::pg_catalog.regtype"""),
            {"function": FUNCTION},
        )
        .mappings()
        .one()
    )
    need(
        dict(proc)
        == {
            "body": BODY,
            "security_definer": False,
            "volatility": "v",
            "config": ["search_path=pg_catalog"],
            "language": "plpgsql",
            "owner_member": False,
        },
        "Source audit immutability function differs",
    )
    triggers = (
        db.execute(
            text("""SELECT t.tgname,t.tgtype,t.tgenabled,t.tgnargs,t.tgqual IS NULL AS unconditional,
        t.tgfoid=pg_catalog.to_regprocedure(:function) AS exact_function FROM pg_catalog.pg_trigger t
        WHERE t.tgrelid=pg_catalog.to_regclass(:table) AND NOT t.tgisinternal ORDER BY t.tgname"""),
            {"table": "public." + TABLE, "function": "public." + FUNCTION + "()"},
        )
        .mappings()
        .all()
    )
    expected = [
        {
            "tgname": "gm_sql_audit_immutable_row",
            "tgtype": 27,
            "tgenabled": "A",
            "tgnargs": 0,
            "unconditional": True,
            "exact_function": True,
        },
        {
            "tgname": "gm_sql_audit_immutable_truncate",
            "tgtype": 34,
            "tgenabled": "A",
            "tgnargs": 0,
            "unconditional": True,
            "exact_function": True,
        },
    ]
    need([dict(row) for row in triggers] == expected, "Source audit immutability triggers differ")
    inspect_row_capture(db)
    need(schema_sha256(db) == expected_schema_sha256, "Source SQL audit schema pin differs")
    need(
        db.execute(
            text("SELECT pg_catalog.pg_try_advisory_xact_lock(:key)"), {"key": GLOBAL_LOCK}
        ).scalar_one()
        is True,
        "Source audit transaction fence busy",
    )
    return identity_anchor(identity)


def schema_sha256(db):
    return codec.digest(
        {
            "event_table": restore_fence._relation_pin(db, TABLE),
            "row_table": restore_fence._relation_pin(db, ROW_TABLE),
        }
    )


def inspect_row_capture(db):
    columns = db.execute(
        text("""SELECT a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod),a.attnotnull
        FROM pg_catalog.pg_attribute a WHERE a.attrelid=pg_catalog.to_regclass(:table)
        AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum"""),
        {"table": "public." + ROW_TABLE},
    ).all()
    need(
        [tuple(row) for row in columns]
        == [
            ("event_id", "text", True),
            ("transaction_id", "bigint", True),
            ("row_sha256", "text", True),
        ],
        "Source row audit columns differ",
    )
    props = (
        db.execute(
            text("""SELECT c.relkind,c.relpersistence,c.relrowsecurity,c.relforcerowsecurity,
        pg_catalog.pg_has_role((SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user),c.relowner,'MEMBER') AS owner_member,
        pg_catalog.has_table_privilege(current_user,c.oid,'SELECT') AS can_select,
        pg_catalog.has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER') AS can_mutate,
        EXISTS(SELECT 1 FROM pg_catalog.pg_inherits WHERE inhparent=c.oid OR inhrelid=c.oid) AS inherited,
        EXISTS(SELECT 1 FROM pg_catalog.pg_index i WHERE i.indrelid=c.oid AND i.indisprimary AND i.indisvalid AND i.indisready AND i.indnkeyatts=3 AND i.indkey::text='1 2 3' AND i.indpred IS NULL) AS exact_primary_key
        FROM pg_catalog.pg_class c WHERE c.oid=pg_catalog.to_regclass(:table)"""),
            {"table": "public." + ROW_TABLE},
        )
        .mappings()
        .one()
    )
    need(
        dict(props)
        == {
            "relkind": "r",
            "relpersistence": "p",
            "relrowsecurity": False,
            "relforcerowsecurity": False,
            "owner_member": False,
            "can_select": True,
            "can_mutate": False,
            "inherited": False,
            "exact_primary_key": True,
        },
        "Source row audit privileges differ",
    )
    function = (
        db.execute(
            text("""SELECT p.prosrc AS body,p.prosecdef AS security_definer,p.provolatile AS volatility,p.proconfig AS config,
        (SELECT lanname FROM pg_catalog.pg_language WHERE oid=p.prolang) AS language,
        p.proowner=c.relowner AS table_owner,
        pg_catalog.pg_has_role((SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user),p.proowner,'MEMBER') AS owner_member
        FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
        JOIN pg_catalog.pg_class c ON c.oid=pg_catalog.to_regclass(:table)
        WHERE n.nspname='public' AND p.proname=:function AND p.pronargs=0 AND p.prorettype='pg_catalog.trigger'::pg_catalog.regtype"""),
            {"table": "public." + ROW_TABLE, "function": ROW_FUNCTION},
        )
        .mappings()
        .one()
    )
    need(
        dict(function)
        == {
            "body": ROW_BODY,
            "security_definer": True,
            "volatility": "v",
            "config": ["search_path=pg_catalog"],
            "language": "plpgsql",
            "table_owner": True,
            "owner_member": False,
        },
        "Source deleted-row audit function differs",
    )
    trigger = (
        db.execute(
            text("""SELECT t.tgtype,t.tgenabled,t.tgnargs,t.tgqual IS NULL AS unconditional,
        t.tgconstraint=0 AS unconstrained,t.tgattr::text AS attributes,
        t.tgfoid=pg_catalog.to_regprocedure(:function) AS exact_function FROM pg_catalog.pg_trigger t
        WHERE t.tgrelid='public.ingest_log'::pg_catalog.regclass AND t.tgname='gm_sql_capture_deleted_row' AND NOT t.tgisinternal"""),
            {"function": "public." + ROW_FUNCTION + "()"},
        )
        .mappings()
        .one()
    )
    need(
        dict(trigger)
        == {
            "tgtype": 9,
            "tgenabled": "A",
            "tgnargs": 0,
            "unconditional": True,
            "unconstrained": True,
            "attributes": "",
            "exact_function": True,
        },
        "Source deleted-row audit trigger differs",
    )
    triggers = (
        db.execute(
            text("""SELECT t.tgname,t.tgtype,t.tgenabled,t.tgnargs,t.tgqual IS NULL AS unconditional,
        t.tgfoid=pg_catalog.to_regprocedure(:function) AS exact_function FROM pg_catalog.pg_trigger t
        WHERE t.tgrelid=pg_catalog.to_regclass(:table) AND NOT t.tgisinternal ORDER BY t.tgname"""),
            {"table": "public." + ROW_TABLE, "function": "public." + FUNCTION + "()"},
        )
        .mappings()
        .all()
    )
    expected = [
        {
            "tgname": "gm_sql_audit_immutable_row",
            "tgtype": 27,
            "tgenabled": "A",
            "tgnargs": 0,
            "unconditional": True,
            "exact_function": True,
        },
        {
            "tgname": "gm_sql_audit_immutable_truncate",
            "tgtype": 34,
            "tgenabled": "A",
            "tgnargs": 0,
            "unconditional": True,
            "exact_function": True,
        },
    ]
    need(
        [dict(row) for row in triggers] == expected, "Source row audit immutability triggers differ"
    )


def native_row_hashes(db, candidate, rows, checkpoint):
    need(
        candidate.source == "gateway" and candidate.dataset == "ingest_log" and 0 < len(rows) <= 32,
        "Exact SQL row audit scope required",
    )
    result = []
    for item in rows:
        checkpoint()
        value = db.execute(
            text("""SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(pg_catalog.row_to_json(t)::text,'UTF8')),'hex')
            FROM public.ingest_log t WHERE t.measurement_id=:id"""),
            {"id": item["body"]["row"]["measurement_id"]},
        ).scalar_one()
        need(type(value) is str and HEX.fullmatch(value), "Native SQL row hash unavailable")
        result.append(value)
    need(len(set(result)) == len(result), "Native SQL row hash collision")
    return sorted(result)


def arm_row_trigger(db, body):
    event = event_id(body)
    result = db.execute(
        text("SELECT pg_catalog.set_config('greenmind.sql_audit_event',:event,true)"),
        {"event": event},
    ).scalar_one()
    need(result == event, "Source row audit context differs")


def verify_row_trigger(db, body, transaction_id):
    rows = db.execute(
        text("""SELECT row_sha256 FROM public.greenmind_sql_deletion_row_audit_v1
        WHERE event_id=:event AND transaction_id=:txid ORDER BY row_sha256 LIMIT 33"""),
        {"event": event_id(body), "txid": int(transaction_id)},
    ).all()
    need(
        [row[0] for row in rows] == body["source_row_sha256"],
        "Source DELETE trigger row audit differs",
    )


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def event_id(body):
    return hashlib.sha256(canonical(body).encode()).hexdigest()


def insert(db, kind, body):
    need(
        kind in {"genesis", "commit"}
        and type(body) is dict
        and EPOCH.fullmatch(body.get("epoch", "")),
        "Invalid SQL audit event",
    )
    raw = canonical(body)
    need(len(raw.encode()) <= 8192, "SQL audit event too large")
    event = event_id(body)
    row = db.execute(
        text(
            "INSERT INTO public.greenmind_sql_deletion_audit_v1(event_id,epoch,kind,body) VALUES(:id,:epoch,:kind,:body) RETURNING transaction_id::text"
        ),
        {"id": event, "epoch": body["epoch"], "kind": kind, "body": raw},
    ).scalar_one()
    need(
        row == db.execute(text("SELECT pg_catalog.txid_current()::text")).scalar_one(),
        "Audit not bound to current Source transaction",
    )
    return event


def read(db, kind, body):
    event = event_id(body)
    row = db.execute(
        text(
            "SELECT epoch,kind,CASE WHEN pg_catalog.octet_length(body)<=8192 THEN body ELSE NULL END,transaction_id::text FROM public.greenmind_sql_deletion_audit_v1 WHERE event_id=:id"
        ),
        {"id": event},
    ).one_or_none()
    need(
        row is not None
        and tuple(row[:3]) == (body["epoch"], kind, canonical(body))
        and str(row[3]).isdecimal(),
        "Matching immutable Source audit absent",
    )
    return event
