"""Bounded Source SQL audit V3. DDL is review-only and never executed here.

V3 is intentionally separate from the existing V1 guard: generating this plan
neither enables SQL deletion nor proves native Source or backup acceptance.
"""

import hashlib
import json
import re

from app.raw_archive.policy import ArchiveBlocked

NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}\Z")
HEX32 = re.compile(r"[a-f0-9]{32}\Z")
HEX64 = re.compile(r"[a-f0-9]{64}\Z")
MAX_OPERATIONS = 128
MAX_ROWS = 32
MAX_SNAPSHOT_BYTES = 1024 * 1024


def need(condition, message):
    if not condition:
        raise ArchiveBlocked(message)


def ddl_plan(writer_role, epoch, genesis_sha256):
    """Return an additive fixed-generation plan, not an installation API.

    A DBA inserts a pinned approval in the same fenced transaction as its
    authorization. The writer cannot issue those INSERTs or alter audit data.
    """
    need(type(writer_role) is str and NAME.fullmatch(writer_role), "Exact writer role required")
    need(type(epoch) is str and HEX32.fullmatch(epoch), "Exact SQL audit epoch required")
    need(
        type(genesis_sha256) is str and HEX64.fullmatch(genesis_sha256),
        "Pinned SQL audit genesis required",
    )
    ddl = f"""BEGIN;
SET LOCAL lock_timeout='150ms';
SET LOCAL statement_timeout='8s';
CREATE TABLE public.gm_sql_v3_generation (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 epoch text NOT NULL CHECK (epoch = '{epoch}'),
 genesis_sha text NOT NULL CHECK (genesis_sha = '{genesis_sha256}'),
 writer_oid oid NOT NULL
);
INSERT INTO public.gm_sql_v3_generation(singleton,epoch,genesis_sha,writer_oid)
 VALUES(true,'{epoch}','{genesis_sha256}','{writer_role}'::regrole::oid);
CREATE TABLE public.gm_sql_v3_approval (
 slot smallint PRIMARY KEY CHECK (slot BETWEEN 1 AND 128),
 epoch text NOT NULL CHECK (epoch = '{epoch}'),
 writer_oid oid NOT NULL,
 candidate_sha text NOT NULL CHECK (candidate_sha ~ '^[a-f0-9]{{64}}$'),
 tenant_sha text NOT NULL CHECK (tenant_sha ~ '^[a-f0-9]{{64}}$'),
 pk_sha text NOT NULL CHECK (pk_sha ~ '^[a-f0-9]{{64}}$'),
 permit_sha text NOT NULL CHECK (permit_sha ~ '^[a-f0-9]{{64}}$'),
 body text NOT NULL CHECK (octet_length(body) <= 1024 AND body !~ '[[:cntrl:]]'),
 expected text[] NOT NULL,
 start_at timestamptz NOT NULL,
 expires_at timestamptz NOT NULL,
 CHECK (expires_at > start_at AND expires_at <= start_at + interval '110 seconds'),
 CHECK (array_ndims(expected)=1 AND cardinality(expected) BETWEEN 1 AND 32)
);
CREATE TABLE public.gm_sql_v3_event (
 slot smallint PRIMARY KEY REFERENCES public.gm_sql_v3_approval(slot),
 txid bigint NOT NULL UNIQUE,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE public.gm_sql_v3_row (
 slot smallint NOT NULL REFERENCES public.gm_sql_v3_event(slot),
 hash text NOT NULL CHECK (hash ~ '^[a-f0-9]{{64}}$'),
 PRIMARY KEY(slot,hash)
);
CREATE TABLE public.gm_sql_v3_unknown (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 txid bigint NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE FUNCTION public.gm_sql_v3_immutable() RETURNS trigger LANGUAGE plpgsql
 SET search_path=pg_catalog AS $f$ BEGIN RAISE EXCEPTION 'immutable_sql_audit_v3'; END; $f$;
CREATE FUNCTION public.gm_sql_v3_validate_approval() RETURNS trigger LANGUAGE plpgsql
 SET search_path=pg_catalog AS $f$
BEGIN
 PERFORM pg_advisory_xact_lock(5137854358536410436::bigint);
 IF NEW.start_at > clock_timestamp() OR NEW.expires_at <= clock_timestamp()
 OR EXISTS (SELECT 1 FROM unnest(NEW.expected) h WHERE h IS NULL OR h !~ '^[a-f0-9]{{64}}$')
 OR (SELECT count(DISTINCT h) FROM unnest(NEW.expected) h) <> cardinality(NEW.expected)
 OR NOT EXISTS (SELECT 1 FROM public.gm_sql_v3_generation g WHERE g.epoch=NEW.epoch AND g.writer_oid=NEW.writer_oid)
 OR EXISTS (SELECT 1 FROM public.gm_sql_v3_unknown)
 THEN RAISE EXCEPTION 'invalid_sql_audit_v3_approval'; END IF;
 RETURN NEW;
END; $f$;
CREATE TRIGGER gm_v3_approval_check BEFORE INSERT ON public.gm_sql_v3_approval
 FOR EACH ROW EXECUTE FUNCTION public.gm_sql_v3_validate_approval();
CREATE FUNCTION public.gm_sql_v3_arm(p_slot smallint, p_permit_sha text) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $f$
DECLARE a public.gm_sql_v3_approval%ROWTYPE;
BEGIN
 PERFORM pg_advisory_xact_lock(5137854358536410436::bigint);
 IF EXISTS (SELECT 1 FROM public.gm_sql_v3_unknown) THEN RAISE EXCEPTION 'unknown_sql_audit_v3'; END IF;
 SELECT * INTO STRICT a FROM public.gm_sql_v3_approval WHERE slot=p_slot;
 IF a.writer_oid <> (SELECT oid FROM pg_roles WHERE rolname=session_user)
 OR a.permit_sha <> p_permit_sha OR clock_timestamp() < a.start_at OR clock_timestamp() >= a.expires_at
 THEN RAISE EXCEPTION 'invalid_sql_audit_v3_permit'; END IF;
 INSERT INTO public.gm_sql_v3_event(slot,txid) VALUES(a.slot,txid_current());
END; $f$;
CREATE FUNCTION public.gm_sql_v3_capture() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog AS $f$
DECLARE chosen smallint; expected text[]; rowhash text;
BEGIN
 IF TG_RELID <> 'public.ingest_log'::regclass OR TG_OP <> 'DELETE' OR TG_LEVEL <> 'ROW' OR TG_WHEN <> 'AFTER' OR TG_NARGS <> 0
 THEN RAISE EXCEPTION 'invalid_sql_audit_v3_trigger'; END IF;
 PERFORM pg_advisory_xact_lock(5137854358536410436::bigint);
 SELECT e.slot,a.expected INTO chosen,expected FROM public.gm_sql_v3_event e
 JOIN public.gm_sql_v3_approval a ON a.slot=e.slot
 WHERE e.txid=txid_current() AND a.writer_oid=(SELECT oid FROM pg_roles WHERE rolname=session_user);
 IF chosen IS NULL THEN
   INSERT INTO public.gm_sql_v3_unknown(singleton,txid) VALUES(true,txid_current()) ON CONFLICT(singleton) DO NOTHING;
 ELSE
   rowhash := encode(sha256(convert_to(row_to_json(OLD)::text,'UTF8')),'hex');
   IF NOT rowhash=ANY(expected) THEN RAISE EXCEPTION 'unapproved_sql_audit_v3_row'; END IF;
   INSERT INTO public.gm_sql_v3_row(slot,hash) VALUES(chosen,rowhash);
 END IF;
 RETURN OLD;
END; $f$;
CREATE FUNCTION public.gm_sql_v3_complete() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog AS $f$
DECLARE required text[]; actual text[]; expiry timestamptz;
BEGIN
 SELECT expected,expires_at INTO STRICT required,expiry FROM public.gm_sql_v3_approval WHERE slot=NEW.slot;
 IF clock_timestamp() >= expiry THEN RAISE EXCEPTION 'expired_sql_audit_v3'; END IF;
 SELECT array_agg(hash ORDER BY hash) INTO actual FROM public.gm_sql_v3_row WHERE slot=NEW.slot;
 IF actual IS DISTINCT FROM ARRAY(SELECT h FROM unnest(required) h ORDER BY h)
 THEN RAISE EXCEPTION 'incomplete_sql_audit_v3'; END IF;
 RETURN NULL;
END; $f$;
CREATE CONSTRAINT TRIGGER gm_v3_complete AFTER INSERT ON public.gm_sql_v3_event
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.gm_sql_v3_complete();
CREATE TRIGGER gm_v3_capture AFTER DELETE ON public.ingest_log FOR EACH ROW EXECUTE FUNCTION public.gm_sql_v3_capture();
ALTER TABLE public.ingest_log ENABLE ALWAYS TRIGGER gm_v3_capture;
"""
    for table in ("generation", "approval", "event", "row", "unknown"):
        name = "gm_sql_v3_" + table
        ddl += f'''CREATE TRIGGER gm_v3_immutable BEFORE UPDATE OR DELETE ON public.{name} FOR EACH ROW EXECUTE FUNCTION public.gm_sql_v3_immutable();
CREATE TRIGGER gm_v3_immutable_truncate BEFORE TRUNCATE ON public.{name} FOR EACH STATEMENT EXECUTE FUNCTION public.gm_sql_v3_immutable();
ALTER TABLE public.{name} ENABLE ALWAYS TRIGGER gm_v3_immutable;
ALTER TABLE public.{name} ENABLE ALWAYS TRIGGER gm_v3_immutable_truncate;
REVOKE ALL ON public.{name} FROM PUBLIC;
GRANT SELECT ON public.{name} TO "{writer_role}";
'''
    ddl += f'''REVOKE ALL ON FUNCTION public.gm_sql_v3_arm(smallint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.gm_sql_v3_arm(smallint,text) TO "{writer_role}";
COMMIT;'''
    return {
        "format": "greenmind.sql-source-audit-ddl.v3",
        "status": "REVIEW_ONLY",
        "executed": False,
        "epoch": epoch,
        "genesis_sha256": genesis_sha256,
        "writer_role": writer_role,
        "ddl": ddl,
        "ddl_sha256": hashlib.sha256(ddl.encode()).hexdigest(),
    }


def schema_sha256(db):
    """Pin actual table/trigger/function definitions, ownership and grants."""
    from sqlalchemy import text

    from . import codec, restore_fence

    tables = (
        "gm_sql_v3_generation",
        "gm_sql_v3_approval",
        "gm_sql_v3_event",
        "gm_sql_v3_row",
        "gm_sql_v3_unknown",
        "ingest_log",
    )
    functions = db.execute(
        text("""SELECT p.proname,pg_catalog.pg_get_function_identity_arguments(p.oid),
          pg_catalog.pg_get_functiondef(p.oid),p.proowner::bigint,p.proacl::text
          FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname='public' AND p.proname IN
          ('gm_sql_v3_arm','gm_sql_v3_capture','gm_sql_v3_complete',
           'gm_sql_v3_immutable','gm_sql_v3_validate_approval') ORDER BY p.proname""")
    ).all()
    need(len(functions) == 5, "V3 audit function roster differs")
    return codec.digest(
        {
            "relations": {name: restore_fence._relation_pin(db, name) for name in tables},
            "functions": [list(row) for row in functions],
        }
    )


def inspect_writer(db, writer_role, writer_oid):
    """Read actual Source role and table privileges; a pin alone is not authority."""
    from sqlalchemy import text

    role = db.execute(
        text("""SELECT oid::bigint,rolsuper,rolbypassrls,rolreplication,
        rolcreaterole,rolcreatedb,rolcanlogin FROM pg_catalog.pg_roles WHERE rolname=:name"""),
        {"name": writer_role},
    ).one_or_none()
    need(
        role is not None and tuple(role) == (writer_oid, False, False, False, False, False, True),
        "V3 writer role privileges differ",
    )
    for table in ("generation", "approval", "event", "row", "unknown"):
        name = "public.gm_sql_v3_" + table
        privileges = db.execute(
            text("""SELECT
            pg_catalog.has_table_privilege(:role, c.oid, 'SELECT'),
            pg_catalog.has_table_privilege(:role, c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER'),
            pg_catalog.pg_has_role(:role, c.relowner, 'MEMBER')
            FROM pg_catalog.pg_class c WHERE c.oid=pg_catalog.to_regclass(:name)"""),
            {"role": writer_role, "name": name},
        ).one_or_none()
        need(
            privileges is not None and tuple(privileges) == (True, False, False),
            "V3 writer table privileges differ",
        )
    can_arm = db.execute(
        text(
            "SELECT pg_catalog.has_function_privilege(:role,'public.gm_sql_v3_arm(smallint,text)','EXECUTE')"
        ),
        {"role": writer_role},
    ).scalar_one()
    need(can_arm is True, "V3 writer factory privilege missing")


def validate_operation_body(raw, candidate_sha, permit_sha):
    """DBA approval contains the exact public V3 pilot authorization binding."""
    need(
        type(raw) is str and len(raw.encode()) <= 1024 and all(ord(c) >= 32 for c in raw),
        "Bounded V3 audit body required",
    )
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ArchiveBlocked("V3 audit operation encoding differs") from exc
    fields = {
        "format",
        "pilot_id",
        "candidate",
        "authorization_sha256",
        "runtime_sha256",
        "bindings_sha256",
        "proof_contract_sha256",
        "fence_contract_sha256",
    }
    need(
        type(value) is dict
        and set(value) == fields
        and value["format"] == "greenmind.sql-audit-operation.v3"
        and type(value["pilot_id"]) is str
        and HEX32.fullmatch(value["pilot_id"])
        and value["candidate"] == candidate_sha
        and value["authorization_sha256"] == permit_sha
        and all(
            type(value[k]) is str and HEX64.fullmatch(value[k])
            for k in fields - {"format", "pilot_id"}
        )
        and json.dumps(value, sort_keys=True, separators=(",", ":")) == raw,
        "V3 audit operation scope differs",
    )
    return value


def check_lease_expiry(expires, now, remaining):
    need(
        type(remaining) in (int, float)
        and remaining > 0
        and 0 < (expires - now).total_seconds() <= remaining,
        "V3 permit outlives Source lease",
    )


def provision(
    engine, *, permit_path, permit_sha256, binding_path, binding_sha256, lease_session, checkpoint
):
    """Source-root DBA provisioner; caller must own the authenticated Archive handoff.

    This is deliberately not exposed as an automatic CLI, nor does it install
    DDL. Root permit/Source binding and the live Source lock are mandatory.
    """
    import os
    import socket
    import time
    from datetime import UTC, datetime
    from pathlib import Path

    from sqlalchemy import text

    from app.raw_archive import scientific_capture
    from app.raw_archive import scientific_scope_control as control

    from .sql_deletion_guard import _identity

    need(os.geteuid() == 0 and callable(checkpoint), "Source root execution required")
    permit = control._private_json(permit_path, permit_sha256, limit=16384)
    binding = control._private_json(binding_path, binding_sha256, limit=16384)
    fields = {
        "format",
        "lease_session",
        "producer_sha256",
        "source_identity",
        "schema_sha256",
        "writer_role",
        "writer_oid",
        "epoch",
        "slot",
        "candidate_sha",
        "tenant_sha",
        "pk_sha",
        "permit_sha",
        "body",
        "expected",
        "start_at",
        "expires_at",
    }
    need(
        set(permit) == fields and permit["format"] == "greenmind.sql-audit-v3-source-permit.v1",
        "Exact V3 root permit required",
    )
    need(
        type(lease_session) is str
        and HEX32.fullmatch(lease_session)
        and permit["lease_session"] == lease_session,
        "Source lease nonce differs",
    )
    need(
        permit["producer_sha256"] == hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "V3 provisioner code pin differs",
    )
    need(
        type(permit["source_identity"]) is dict and HEX64.fullmatch(permit["schema_sha256"] or ""),
        "Native Source pins required",
    )
    need(
        type(permit["writer_role"]) is str
        and NAME.fullmatch(permit["writer_role"])
        and type(permit["writer_oid"]) is int
        and permit["writer_oid"] > 0,
        "Exact writer role pin required",
    )
    need(
        type(permit["epoch"]) is str
        and HEX32.fullmatch(permit["epoch"])
        and type(permit["slot"]) is int
        and 1 <= permit["slot"] <= MAX_OPERATIONS,
        "V3 epoch or capacity differs",
    )
    need(
        all(
            type(permit[k]) is str and HEX64.fullmatch(permit[k])
            for k in ("candidate_sha", "tenant_sha", "pk_sha", "permit_sha")
        ),
        "Exact V3 scope hashes required",
    )
    validate_operation_body(permit["body"], permit["candidate_sha"], permit["permit_sha"])
    expected = permit["expected"]
    need(
        type(expected) is list
        and 1 <= len(expected) <= MAX_ROWS
        and all(type(x) is str and HEX64.fullmatch(x) for x in expected)
        and expected == sorted(set(expected)),
        "Exact unique V3 native row hashes required",
    )
    try:
        start = datetime.fromisoformat(permit["start_at"])
        expires = datetime.fromisoformat(permit["expires_at"])
        now = datetime.now(UTC)
        valid = (
            start.utcoffset() is not None
            and expires.utcoffset() is not None
            and start <= now < expires
            and 0 < (expires - start).total_seconds() <= 110
        )
    except (TypeError, ValueError):
        valid = False
    need(valid, "V3 Source permit time differs")
    locked = control._source_lock(full=True)
    need(
        locked["device"] == binding.get("lock_device")
        and locked["inode"] == binding.get("lock_inode")
        and locked["uid"] == binding.get("lease_uid")
        and locked["command_sha256"] == binding.get("lease_command_sha256")
        and locked["executable_sha256"] == binding.get("lease_executable_sha256")
        and socket.gethostname() == binding.get("hostname")
        and hashlib.sha256(Path("/etc/machine-id").read_bytes()).hexdigest()
        == binding.get("machine_sha256")
        and Path("/proc/sys/kernel/random/boot_id").read_text().strip() == binding.get("boot_id"),
        "Source lease binding differs",
    )
    ticks = os.sysconf("SC_CLK_TCK")
    need(type(ticks) is int and ticks > 0, "Source clock unavailable")
    deadline = min(time.monotonic() + 95, locked["start_ticks"] / ticks + 110)
    remaining = deadline - time.monotonic()
    check_lease_expiry(expires, datetime.now(UTC), remaining)
    holder = {k: locked[k] for k in ("device", "inode", "pid", "uid", "start_ticks")}

    def check():
        checkpoint()
        scientific_capture._cgroup_limits()
        need(
            time.monotonic() < deadline and control._source_lock(full=False) == holder,
            "Source lease expired or changed",
        )
        need(datetime.now(UTC) < expires, "Source permit expired")

    check()
    with engine.begin() as db:
        need(
            db.dialect.name == "postgresql" and _identity(db) == permit["source_identity"],
            "Source DBA identity differs",
        )
        need(schema_sha256(db) == permit["schema_sha256"], "Source V3 schema differs")
        writer_oid = db.execute(
            text("SELECT oid::bigint FROM pg_catalog.pg_roles WHERE rolname=:name"),
            {"name": permit["writer_role"]},
        ).scalar_one()
        need(writer_oid == permit["writer_oid"], "Source writer role OID differs")
        inspect_writer(db, permit["writer_role"], writer_oid)
        db.execute(text("SET LOCAL lock_timeout='150ms'"))
        db.execute(text("SET LOCAL statement_timeout='8s'"))
        check()
        db.execute(
            text("""INSERT INTO public.gm_sql_v3_approval(slot,epoch,writer_oid,candidate_sha,tenant_sha,pk_sha,permit_sha,body,expected,start_at,expires_at)
            VALUES(:slot,:epoch,:writer_oid,:candidate_sha,:tenant_sha,:pk_sha,:permit_sha,:body,CAST(:expected AS text[]),:start_at,:expires_at)"""),
            {
                k: permit[k]
                for k in (
                    "slot",
                    "epoch",
                    "writer_oid",
                    "candidate_sha",
                    "tenant_sha",
                    "pk_sha",
                    "permit_sha",
                    "body",
                    "expected",
                    "start_at",
                    "expires_at",
                )
            },
        )
        need(
            schema_sha256(db) == permit["schema_sha256"]
            and _identity(db) == permit["source_identity"],
            "Source V3 schema or DBA changed during provision",
        )
        check()
    return {
        "format": "greenmind.sql-audit-v3-provision-result.v1",
        "permit_sha256": permit_sha256,
        "slot": permit["slot"],
        "epoch": permit["epoch"],
    }
