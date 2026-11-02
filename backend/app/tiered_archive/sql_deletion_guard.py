"""Native fail-closed proof factory for one Gateway ingest-log pilot.

No DDL is executed here. Installation requires separately reviewed privileges,
source admission, exact candidate evidence and a planned writer-fence change.
"""

import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.raw_archive import snapshot_api
from app.raw_archive import storage as raw_storage
from app.raw_archive.policy import ArchiveBlocked, checksum
from app.raw_archive.snapshot_api import SnapshotAPI, check_loaded_function_code
from app.raw_archive.storage import StorageBox

from . import (
    backup,
    codec,
    restore_baseline,
    restore_fence,
    restore_inputs,
    restore_session,
    runtime,
)
from .ledger import Ledger, _private_read
from .policy import utc
from .source import DATASETS, Source


def need(value, message):
    if not value:
        raise ArchiveBlocked(message)


def _sha(value):
    return type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def fence_plan(candidate, lock_key):
    """Return deterministic additive SQL; this function never connects or executes it.

    Time-scoped ingest writes and all Gateway/Zone parent writes use the same
    lock; parent locks are intentionally wider than one tenant to prevent moves.
    """
    need(
        candidate.source == "gateway"
        and candidate.dataset == "ingest_log"
        and candidate.kind == "logs"
        and type(lock_key) is int
        and -(2**63) <= lock_key < 2**63,
        "Only exact Gateway ingest-log fence is implemented",
    )
    name = "greenmind_sql_pilot_" + candidate.identity[:16]
    start, end = utc(candidate.start).isoformat(), utc(candidate.end).isoformat()
    need(
        start < end and all(re.fullmatch(r"[0-9T:.+\-]+", x) for x in (start, end)),
        "Safe UTC pilot bounds required",
    )

    def in_range(prefix):
        return (
            f"({prefix}.created_at >= TIMESTAMPTZ '{start}' AND "
            f"{prefix}.created_at < TIMESTAMPTZ '{end}')"
        )

    body = (
        "\nBEGIN\n"
        "  IF TG_TABLE_NAME = 'ingest_log' AND TG_OP <> 'TRUNCATE' THEN\n"
        "    IF TG_OP = 'INSERT' THEN\n"
        f"      IF {in_range('NEW')} THEN PERFORM pg_catalog.pg_advisory_xact_lock({lock_key}::bigint); END IF;\n"
        "    ELSIF TG_OP = 'DELETE' THEN\n"
        f"      IF {in_range('OLD')} THEN PERFORM pg_catalog.pg_advisory_xact_lock({lock_key}::bigint); END IF;\n"
        "    ELSIF TG_OP = 'UPDATE' THEN\n"
        f"      IF {in_range('OLD')} OR {in_range('NEW')} THEN PERFORM pg_catalog.pg_advisory_xact_lock({lock_key}::bigint); END IF;\n"
        "    END IF;\n"
        "  ELSE\n"
        f"    PERFORM pg_catalog.pg_advisory_xact_lock({lock_key}::bigint);\n"
        "  END IF;\n"
        "  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;\n"
        "  IF TG_OP = 'TRUNCATE' THEN RETURN NULL; END IF;\n"
        "  RETURN NEW;\nEND\n"
    )
    row_name = name + "_row"
    truncate_name = name + "_truncate"
    ddl = [
        "BEGIN;",
        "SET LOCAL lock_timeout='150ms';",
        "SET LOCAL statement_timeout='8s';",
        f'CREATE FUNCTION public."{name}"() RETURNS trigger LANGUAGE plpgsql '
        f"VOLATILE SECURITY INVOKER SET search_path=pg_catalog AS $gm${body}$gm$;",
    ]
    for table in ("ingest_log", "gateway", "zone"):
        ddl.extend(
            [
                f'CREATE TRIGGER "{row_name}" BEFORE INSERT OR UPDATE OR DELETE ON public."{table}" '
                f'FOR EACH ROW EXECUTE FUNCTION public."{name}"();',
                f'CREATE TRIGGER "{truncate_name}" BEFORE TRUNCATE ON public."{table}" '
                f'FOR EACH STATEMENT EXECUTE FUNCTION public."{name}"();',
                f'ALTER TABLE public."{table}" ENABLE ALWAYS TRIGGER "{row_name}";',
                f'ALTER TABLE public."{table}" ENABLE ALWAYS TRIGGER "{truncate_name}";',
            ]
        )
    ddl.append("COMMIT;")
    spec = {
        "format": "greenmind.sql-writer-fence.ingest-v1",
        "candidate": candidate.identity,
        "lock_key": lock_key,
        "function": name,
        "body": body,
        "row_trigger": row_name,
        "truncate_trigger": truncate_name,
        "tables": ["ingest_log", "gateway", "zone"],
    }
    return {"spec": spec, "fence_contract_sha256": codec.digest(spec), "ddl": "\n".join(ddl)}


def prepare_fence(candidate):
    """Build the exact SQL pilot plan without opening a database."""
    from .sql_deletion import _lock_key

    candidate.validate()
    return fence_plan(candidate, _lock_key(candidate))


def _identity(db):
    return dict(
        db.execute(
            text("""SELECT current_database() AS database,
       (SELECT oid::bigint FROM pg_database WHERE datname=current_database()) AS database_oid,
       current_user AS role, session_user AS session_role,
       (SELECT oid::bigint FROM pg_roles WHERE rolname=current_user) AS role_oid,
       (SELECT NOT (rolsuper OR rolbypassrls OR rolreplication OR rolcreaterole OR rolcreatedb) FROM pg_roles
        WHERE rolname=current_user) AS restricted_role,
       current_setting('server_version_num')::integer AS version,
       current_setting('session_replication_role') AS replication_role,
       current_setting('transaction_isolation') AS isolation,
       current_setting('transaction_read_only') AS read_only,
       current_schemas(true) AS search_path,
       inet_server_addr()::text AS server_address,inet_server_port() AS server_port,
       pg_postmaster_start_time()::text AS postmaster_start""")
        )
        .mappings()
        .one()
    )


def _catalog(db):
    return codec.digest(
        {name: restore_fence._relation_pin(db, name) for name in ("ingest_log", "gateway", "zone")}
    )


def _fence(db, spec, owner_oid, *, audit_trigger="gm_sql_capture_deleted_row"):
    proc = (
        db.execute(
            text("""SELECT p.prosrc AS body,p.proowner::bigint AS owner,
        p.prosecdef AS security_definer,p.provolatile AS volatility,p.proconfig AS config
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname=:name AND p.pronargs=0
        AND p.prorettype='pg_catalog.trigger'::regtype"""),
            {"name": spec["function"]},
        )
        .mappings()
        .one_or_none()
    )
    need(
        proc is not None
        and proc["body"] == spec["body"]
        and proc["owner"] == owner_oid
        and proc["security_definer"] is False
        and proc["volatility"] == "v"
        and proc["config"] == ["search_path=pg_catalog"],
        "SQL writer fence function differs",
    )
    for table in spec["tables"]:
        inherited = db.execute(
            text("""SELECT EXISTS(SELECT 1 FROM pg_inherits
            WHERE inhrelid=to_regclass(:table) OR inhparent=to_regclass(:table))"""),
            {"table": "public." + table},
        ).scalar_one()
        need(inherited is False, "Unfenced inherited source table")
        rows = (
            db.execute(
                text("""SELECT t.tgname,t.tgtype,t.tgenabled,t.tgnargs,
            t.tgqual IS NULL AS unconditional,t.tgconstraint=0 AS unconstrained,
            t.tgfoid=to_regprocedure(:function) AS exact_function, t.tgattr::text AS attributes
            FROM pg_trigger t WHERE t.tgrelid=to_regclass(:table) AND NOT t.tgisinternal
            ORDER BY t.tgname"""),
                {"table": "public." + table, "function": "public." + spec["function"] + "()"},
            )
            .mappings()
            .all()
        )
        selected = {
            row["tgname"]: row
            for row in rows
            if row["tgname"] in {spec["row_trigger"], spec["truncate_trigger"]}
        }
        need(
            set(selected) == {spec["row_trigger"], spec["truncate_trigger"]},
            "SQL writer fence trigger missing",
        )
        for name, kind in ((spec["row_trigger"], 31), (spec["truncate_trigger"], 34)):
            item = selected[name]
            need(
                item["tgtype"] == kind
                and item["tgenabled"] == "A"
                and item["tgnargs"] == 0
                and item["unconditional"] is True
                and item["unconstrained"] is True
                and item["exact_function"] is True
                and item["attributes"] == "",
                "SQL writer fence trigger differs",
            )
        if table == "ingest_log":
            extra = [row for row in rows if row["tgname"] == audit_trigger]
            need(
                len(rows) == 3
                and len(extra) == 1
                and extra[0]["tgtype"] == 9
                and extra[0]["tgenabled"] == "A"
                and extra[0]["tgnargs"] == 0
                and extra[0]["unconditional"] is True
                and extra[0]["unconstrained"] is True
                and extra[0]["exact_function"] is False
                and extra[0]["attributes"] == "",
                "Deleted-row audit trigger or target trigger roster differs",
            )
    extras = db.execute(
        text("""SELECT count(*) FROM pg_constraint WHERE contype='f'
        AND confrelid='public.ingest_log'::regclass AND confdeltype NOT IN ('a','r')""")
    ).scalar_one()
    shape = db.execute(
        text("""SELECT c.relkind,c.relrowsecurity,c.relforcerowsecurity,
        EXISTS(SELECT 1 FROM pg_inherits i WHERE i.inhrelid=c.oid OR i.inhparent=c.oid)
        FROM pg_class c WHERE c.oid='public.ingest_log'::regclass""")
    ).one()
    need(
        extras == 0 and tuple(shape) == ("r", False, False, False),
        "Target foreign-key, RLS or physical inheritance scope unproved",
    )


def _live_rows(db, candidate, adapter, checkpoint):
    spec = DATASETS["ingest_log"]
    # Fixed source expressions and exact 33-row cap; never interpolate candidate values.
    query = text("""SELECT t.*,CAST(z.organization_id AS text) AS _archive_org,
       CAST(t.gateway_id AS text) AS _archive_sensor FROM public.ingest_log t
       JOIN public.gateway g ON g.id=t.gateway_id JOIN public.zone z ON z.id=g.zone_id
       WHERE CAST(z.organization_id AS text)=:org AND t.created_at>=:start AND t.created_at<:end
       LIMIT 33""")
    rows = (
        db.execute(
            query, {"org": candidate.organization, "start": candidate.start, "end": candidate.end}
        )
        .mappings()
        .all()
    )
    need(len(rows) == len(adapter.expected) <= 32, "Full SQL candidate partition changed")
    live = {}
    for row in rows:
        checkpoint()
        item = dict(row)
        org, sensor = item.pop("_archive_org"), item.pop("_archive_sensor")
        identity = codec.digest({key: item[key] for key in spec.keys})
        need(identity not in live, "Duplicate live SQL primary key")
        live[identity] = (org, sensor, codec.encode(item))
    for expected in adapter.expected:
        checkpoint()
        actual = live.pop(expected["identity"], None)
        need(
            actual
            == (
                expected["organization"],
                str(expected["sensor"]),
                codec.encode(expected["body"]["row"]),
            )
            and expected["body"]["context"] == {},
            "Live SQL row, context or tenant differs",
        )
    need(not live, "Unarchived live SQL rows in candidate partition")


def _private_json(path, sha):
    need(_sha(sha), "Pinned evidence SHA required")
    path = Path(path)
    frozen = restore_inputs.frozen(path)
    raw = _private_read(path, 1024 * 1024)
    need(restore_inputs.frozen(path) == frozen, "SQL evidence file generation changed")
    info = path.stat(follow_symlinks=False)
    need(
        info.st_uid == 0
        and stat.S_IMODE(info.st_mode) == 0o400
        and info.st_nlink == 1
        and hashlib.sha256(raw).hexdigest() == sha,
        "Root-frozen SQL evidence required",
    )
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise ArchiveBlocked("Invalid pinned SQL evidence") from exc
    need(type(value) is dict, "SQL evidence object required")
    return value


def inspect_source_pins(db, candidate, fence_owner_oid, *, checkpoint):
    """Observe source/fence pins; no acceptance, source writes, or DDL."""
    from .sql_deletion import _lock_key

    need(
        callable(checkpoint) and db.dialect.name == "postgresql",
        "Native source inspection required",
    )
    checkpoint()
    plan = prepare_fence(candidate)
    _fence(db, plan["spec"], fence_owner_oid)
    identity = _identity(db)
    catalog = _catalog(db)
    from . import sql_deletion_audit

    audit_schema = sql_deletion_audit.schema_sha256(db)
    sql_deletion_audit.inspect(db, audit_schema, identity)
    checkpoint()
    need(plan["spec"]["lock_key"] == _lock_key(candidate), "Fence scope changed")
    return {
        "source_identity": identity,
        "source_catalog_sha256": catalog,
        "audit_schema_sha256": audit_schema,
        "fence_contract_sha256": plan["fence_contract_sha256"],
    }


def _snapshot_reference(store, provider, ledger, receipt, checkpoint):
    snapshot = receipt["snapshot"]
    live = provider.read(snapshot["provider_id"])
    need(
        live.get("id") == snapshot["provider_id"]
        and live.get("storage_box") == snapshot["box_id"]
        and live.get("name") == snapshot["name"]
        and utc(live.get("created")) == utc(snapshot["created_at"]),
        "SQL snapshot no longer matches provider",
    )
    reference = receipt["manifest"]
    need(
        reference["format"] == "json" and 0 < reference["bytes"] <= 1024**2,
        "Bounded snapshot manifest required",
    )
    with tempfile.TemporaryDirectory(prefix="sql-proof-", dir=ledger.root) as folder:
        path = Path(folder) / "manifest.json"
        checkpoint()
        store.download(reference["key"], path, reference["bytes"], snapshot=snapshot["name"])
        need(
            path.stat().st_size == reference["bytes"] and checksum(path) == reference["sha256"],
            "SQL snapshot manifest readback differs",
        )
        data = json.loads(path.read_bytes())
        need(
            data.get("identity") == receipt["candidate"]
            and data.get("candidate") == receipt["source"]
            and data.get("files") == receipt["files"]
            and data.get("policy") == receipt["policy"],
            "SQL snapshot manifest binding differs",
        )
    after = provider.read(snapshot["provider_id"])
    need(
        all(after.get(k) == live.get(k) for k in ("id", "storage_box", "name", "created")),
        "SQL snapshot changed during readback",
    )


def _restore(restore_engine, config, candidate, receipt, checkpoint, *, require_pin=True):
    plan = _private_json(config["restore_plan_path"], config["restore_plan_file_sha256"])
    restore_session.validate_plan(plan, config["restore_plan_sha256"])
    need(
        plan["environment"] == candidate.environment
        and plan["source"] == candidate.source
        and plan["dataset"] == candidate.dataset
        and plan["organization"] == candidate.organization
        and plan["expected_pages"] == 1
        and plan["expected_rows"] == candidate.rows,
        "Exact one-candidate native restore plan required",
    )
    validated_url = restore_session.restore_url(str(restore_engine.url))
    need(
        validated_url.database == restore_engine.url.database,
        "Dedicated native restore target required",
    )
    with restore_engine.begin() as db:
        restore_session.admission(db)
        physical = (
            db.execute(
                text("""SELECT inet_server_addr()::text AS address,
            pg_postmaster_start_time()::text AS started""")
            )
            .mappings()
            .one()
        )
        need(
            (physical["address"], physical["started"])
            != (
                config["source_identity"]["server_address"],
                config["source_identity"]["postmaster_start"],
            ),
            "Native restore must not share the Source PostgreSQL instance",
        )
        target_sha = restore_session.target_identity(db)
        need(target_sha == config["restore_target_sha256"], "Native restore target differs")
        session = (
            db.execute(
                text("SELECT * FROM public.greenmind_restore_session WHERE id=:id"),
                {"id": config["restore_session_id"]},
            )
            .mappings()
            .one_or_none()
        )
        need(
            session is not None
            and session["state"] == "verified"
            and session["plan_sha256"] == config["restore_plan_sha256"]
            and session["target_sha256"] == target_sha
            and session["dataset"] == candidate.dataset
            and session["organization"] == candidate.organization
            and session["audit_next_sha256"] is None
            and session["next_sha256"] is None
            and session["pages"] == session["audit_pages"] == 1
            and session["rows"] == session["audit_rows"] == candidate.rows
            and session["batches"] == session["audit_batches"] == 1,
            "Complete native restore session required",
        )
        need(
            db.execute(
                text(
                    "SELECT count(*) FROM public.greenmind_restore_session WHERE state <> 'verified'"
                )
            ).scalar_one()
            == 0,
            "Other native restore sessions are unfinished",
        )
        state = restore_fence.check_audit(db, session["audit_epoch"])
        _, target, children, masters = restore_session.lock_and_tables(db, plan, checkpoint)
        need(
            restore_session.schema_fingerprint([target, *children.values(), *masters.values()])
            == session["schema_sha256"]
            and restore_baseline.anchor(db, masters, state) == session["masters_sha256"],
            "Native restore schema or parent baseline changed",
        )
        page = (
            db.execute(
                text("""SELECT * FROM public.greenmind_restore_page
            WHERE session_id=:id AND candidate_id=:candidate"""),
                {"id": config["restore_session_id"], "candidate": candidate.identity},
            )
            .mappings()
            .one_or_none()
        )
        need(
            page is not None
            and page["rows"] == candidate.rows
            and page["manifest_sha256"] == receipt["manifest"]["sha256"]
            and page["batch_sha256"] == config["restore_batch_sha256"],
            "Native restore page differs from archived candidate",
        )
        audit = (
            db.execute(
                text("""SELECT * FROM public.greenmind_restore_audit
            WHERE session_id=:id AND batch_sha256=:batch"""),
                {"id": config["restore_session_id"], "batch": page["batch_sha256"]},
            )
            .mappings()
            .one_or_none()
        )
        batch = (
            db.execute(
                text("""SELECT * FROM public.greenmind_restore_batch
            WHERE session_id=:id AND sha256=:batch"""),
                {"id": config["restore_session_id"], "batch": page["batch_sha256"]},
            )
            .mappings()
            .one_or_none()
        )
        need(
            audit is not None
            and batch is not None
            and audit["epoch"] == session["audit_epoch"]
            and audit["sequence"] == batch["sequence"] == 0
            and audit["pages"] == batch["pages"] == 1
            and audit["rows"] == batch["rows"] == candidate.rows
            and audit["proof_sha256"] == batch["proof_sha256"],
            "Native restore audit differs",
        )
        evidence_sha = codec.digest(
            {
                "session": dict(session),
                "page": dict(page),
                "batch": dict(batch),
                "audit": dict(audit),
            }
        )
        if require_pin:
            need(
                evidence_sha == config["restore_evidence_sha256"],
                "Native restore evidence digest differs",
            )
        restore_fence.check_audit(db, session["audit_epoch"])
        checkpoint()
        return evidence_sha


def inspect_restore_evidence(restore_engine, config, candidate, receipt, *, checkpoint):
    """Measure real frozen restore evidence for an independently reviewed pin.

    This inspection neither accepts the result nor authorizes deletion.
    """
    return _restore(restore_engine, config, candidate, receipt, checkpoint, require_pin=False)


def _codepin():
    from app.raw_archive import coordination, read_only_box

    from . import (
        backup_scope,
        database_restore,
        ledger,
        recovery,
        registry_backup,
        registry_v5,
        schema_provider,
        scientific_registry_import,
        source,
        sql_audit_v3,
        sql_audit_v3_backup,
        sql_audit_v3_source,
        sql_deletion,
        sql_deletion_audit,
        sql_deletion_scope,
        sql_deletion_v3,
        sql_runtime,
        storage,
    )

    for module, names in (
        (
            sql_deletion,
            (
                "evict",
                "_guard",
                "_scope",
                "_enabled",
                "activation",
                "_budget",
                "_lock_key",
                "_production_guard",
                "_prepare_guard",
                "_begin_guard",
                "_guard_deadline",
                "_audit_begin",
                "_audit_commit",
            ),
        ),
        (
            source,
            (
                "Source.exclusive.__wrapped__",
                "Source.inspect",
                "Source.present",
                "Source.readiness",
                "Source.absent",
                "Source.table",
                "row_identity",
            ),
        ),
        (
            sys.modules[__name__],
            (
                "ProductionGuard.__call__",
                "ProductionGuard.prepare",
                "ProductionGuardV3.prepare",
                "build_guard_v3",
                "ProductionGuard.begin_source",
                "ProductionGuard.check_deadline",
                "ProductionGuard._snapshot_lock",
                "ProductionGuard._receipt_binding",
                "_codepin",
                "_fence",
                "_restore",
                "_snapshot_reference",
                "_live_rows",
                "_identity",
                "_catalog",
                "_private_json",
                "fence_plan",
                "build_guard",
            ),
        ),
        (
            sql_deletion_audit,
            (
                "inspect",
                "inspect_row_capture",
                "schema_sha256",
                "native_row_hashes",
                "arm_row_trigger",
                "verify_row_trigger",
                "insert",
                "read",
                "event_id",
                "canonical",
                "identity_anchor",
                "need",
            ),
        ),
        (
            sql_deletion_scope,
            (
                "bootstrap",
                "reconcile",
                "inventory",
                "current_epoch",
                "commit_body",
                "verify_history",
                "verify_registry_snapshot",
                "_registry_evidence",
                "v3_registry_evidence",
                "v3_inventory",
                "_preflight",
                "_table",
                "RegistryEvidence.__init__",
            ),
        ),
        (sql_deletion_v3, ("activation", "approval_body", "native_approval", "verify_committed_rows", "verify_history", "require_native_restore_evidence", "make_source_permit", "stage_source_approval", "scope_transition", "_guard", "_budget", "evict")),
        (sql_audit_v3, ("schema_sha256", "inspect_writer", "validate_operation_body", "check_lease_expiry", "provision")),
        (sql_audit_v3_source, ("_Docker.connect", "_observe_container", "provision_source")),
        (sql_audit_v3_backup, ("encode", "decode", "capture_structure", "validate", "history_binding", "AuditSnapshotCapture.__init__", "AuditSnapshotCapture.capture", "verify_remote", "verify_gateway_bundle", "record_gateway_readback", "verify_native_rows")),
        (registry_backup, ("restore",)),
        (registry_v5, ("schema", "validate", "canonical")),
        (runtime, ("runtime_sha256", "bindings_sha256")),
        (backup, ("require_current", "dump", "decrypt", "key_from_file")),
        (backup_scope, ("capture_scope",)),
        (schema_provider, ("current_descriptor", "capture")),
        (ledger, ("Ledger.get", "Ledger.save", "Ledger.scope", "_private_read")),
        (codec, ("encode", "digest", "pack")),
        (coordination, ("bound_job_lock",)),
        (read_only_box, ("ReadOnlyBox.__init__", "ReadOnlyBox.download_tiered", "configured_read_only_box", "configured_catalog_read_only_box")),
        (scientific_registry_import, ("_transport", "_provider_proof", "_private_proof")),
        (database_restore, ("_provider_target_guard", "_provider_target_identity")),
        (recovery, ("restore_url",)),
        (
            snapshot_api,
            (
                "SnapshotAPI.read",
                "SnapshotAPI.validate_box",
                "SnapshotAPI.request",
                "require_managed_snapshot_access",
            ),
        ),
        (storage, ("Store.download", "validate_key")),
        (sql_runtime, ("ScopedReadStore.publish", "evict", "_private_file", "_read_only_request")),
        (
            raw_storage,
            (
                "StorageBox.run",
                "StorageBox._download_path",
                "StorageBox._transport_command",
                "StorageBox.quote",
            ),
        ),
        (restore_fence, ("check_audit", "_load", "_relation_pin")),
        (restore_baseline, ("anchor",)),
        (restore_inputs, ("frozen", "protected_parents")),
        (
            restore_session,
            (
                "validate_plan",
                "admission",
                "target_identity",
                "schema_fingerprint",
                "lock_and_tables",
                "audit_restore_batch",
                "load_batch",
                "check_row",
                "page_rows",
                "frozen_batch_inputs",
            ),
        ),
    ):
        path = Path(module.__file__).resolve()
        check_loaded_function_code(module, path, path.read_bytes(), names)


class ProductionGuard:
    """Created only by build_guard; every call repeats the live native evidence checks."""

    def __init__(
        self,
        token,
        *,
        config,
        evidence_path,
        evidence_sha256,
        candidate,
        adapter,
        ledger,
        store,
        provider,
        restore_engine,
        checkpoint,
    ):
        need(token is _TOKEN, "Use the guarded SQL factory")
        self.config, self.evidence_path, self.evidence_sha256 = (
            config,
            evidence_path,
            evidence_sha256,
        )
        self.candidate, self.adapter, self.ledger = candidate, adapter, ledger
        self.store, self.provider, self.restore_engine, self.checkpoint = (
            store,
            provider,
            restore_engine,
            checkpoint,
        )

    @staticmethod
    def _receipt_binding(receipt):
        # State and deletion intent necessarily change after the durable intent.
        return codec.digest(
            {
                key: value
                for key, value in receipt.items()
                if key not in {"state", "deletion_intent"}
            }
        )

    def _snapshot_lock(self, *snapshots):
        from app.raw_archive.coordination import bound_job_lock

        need(
            os.getenv("RAW_ARCHIVE_MANAGED_SCOPE_REQUIRED") == "true"
            and os.getenv("RAW_ARCHIVE_WORKER_ROLE") == "archive",
            "Managed archive job lock required for SQL snapshot proofs",
        )
        bound_job_lock()
        for snapshot in snapshots:
            snapshot_api.require_managed_snapshot_access(snapshot["name"])

    def prepare(self, candidate, receipt, now, permit):
        """Perform expensive provider/readback/native replay before source fencing."""
        need(
            candidate == self.candidate
            and self.adapter.connection is None
            and self.ledger.get(candidate.identity) == receipt
            and _private_json(self.evidence_path, self.evidence_sha256) == self.config
            and permit["proof_contract_sha256"] == self.evidence_sha256,
            "Pinned SQL preflight scope differs",
        )
        self.checkpoint()
        need(
            runtime.runtime_sha256() == permit["runtime_sha256"]
            and runtime.bindings_sha256(self.store) == permit["bindings_sha256"],
            "Running SQL code or destination binding differs",
        )
        _codepin()
        need(
            receipt.get("destination") == self.store.identity == self.config["destination"]
            and receipt["snapshot"]["box_id"] == self.config["box_id"] == self.provider.box_id,
            "SQL archive destination differs",
        )
        current = backup.require_current(
            self.ledger.scope(
                candidate.environment,
                candidate.source,
                "00000000-0000-0000-0000-000000000000",
                "database_backup",
                now - timedelta(hours=24),
                now,
            ),
            source=candidate.source,
            environment=candidate.environment,
            destination=self.store.identity,
            now=now,
            schema_scope=lambda: runtime.current_backup_schema_scope(
                candidate.source, self.checkpoint
            ),
        )
        need(
            current["candidate"] == self.config["backup_candidate"]
            and current["manifest"]["sha256"] == self.config["backup_manifest_sha256"],
            "Current database backup binding differs",
        )
        from . import schema_provider

        need(
            schema_provider.current_descriptor(current["files"], candidate.source),
            "Current scoped provider backup required for SQL eviction",
        )
        self._snapshot_lock(receipt["snapshot"], current["snapshot"])
        _snapshot_reference(self.store, self.provider, self.ledger, receipt, self.checkpoint)
        _snapshot_reference(self.store, self.provider, self.ledger, current, self.checkpoint)
        _restore(self.restore_engine, self.config, candidate, receipt, self.checkpoint)
        plan = _private_json(
            self.config["restore_plan_path"], self.config["restore_plan_file_sha256"]
        )
        replay = restore_session.audit_restore_batch(
            self.restore_engine.url.render_as_string(hide_password=False),
            plan,
            self.config["restore_plan_sha256"],
            self.config["restore_session_id"],
            Path(self.config["restore_batch_path"]),
            self.config["restore_batch_sha256"],
            checkpoint=self.checkpoint,
        )
        need(
            replay.get("phase") == "verified"
            and replay.get("replayed") is True
            and replay.get("audited_pages") == 1
            and replay.get("audited_rows") == candidate.rows
            and replay.get("source_accesses") == 0
            and replay.get("target_data_mutations") == 0,
            "Independent native SQL restore replay incomplete",
        )
        self.checkpoint()
        self._snapshot_lock(receipt["snapshot"], current["snapshot"])
        self._prepared = {
            "receipt": self._receipt_binding(receipt),
            "backup": current,
            "permit": codec.digest(permit),
            "expires": time.monotonic() + 30,
        }
        self._deadline = None

    def begin_source(self):
        """Bound the source transaction independently of the enclosing lease."""
        need(
            hasattr(self, "_prepared")
            and self._deadline is None
            and time.monotonic() < self._prepared["expires"],
            "Fresh SQL preflight required",
        )
        self._deadline = min(self._prepared["expires"], time.monotonic() + 20)

    def check_deadline(self):
        need(
            self._deadline is not None and time.monotonic() < self._deadline,
            "SQL source transaction deadline expired",
        )

    def __call__(self, candidate, receipt, now, db, lock_key, permit):
        from . import backup_scope, sql_deletion

        need(
            hasattr(self, "_prepared")
            and self._deadline is not None
            and time.monotonic() < self._deadline
            and candidate == self.candidate
            and self._prepared["permit"] == codec.digest(permit)
            and self._prepared["receipt"] == self._receipt_binding(receipt)
            and db is self.adapter.connection
            and type(self.adapter) is Source
            and self.ledger.get(candidate.identity) == receipt,
            "Fresh exact SQL preflight, transaction and journal required",
        )
        self.checkpoint()
        need(
            _private_json(self.evidence_path, self.evidence_sha256) == self.config
            and permit["proof_contract_sha256"] == self.evidence_sha256,
            "Pinned SQL proof contract changed",
        )
        need(
            runtime.runtime_sha256() == permit["runtime_sha256"]
            and runtime.bindings_sha256(self.store) == permit["bindings_sha256"],
            "Running SQL code or destination binding differs",
        )
        _codepin()
        expected = self.config["source_identity"]
        identity = _identity(db)
        need(
            identity == expected
            and identity["restricted_role"] is True
            and identity["replication_role"] == "origin"
            and identity["isolation"] == "read committed"
            and identity["read_only"] == "off"
            and identity["search_path"] == ["pg_catalog", "public"]
            and identity["role"] == identity["session_role"],
            "Source database or role identity differs",
        )
        plan = fence_plan(candidate, lock_key)
        need(
            plan["fence_contract_sha256"] == permit["fence_contract_sha256"]
            and permit["fence_contract_sha256"] == self.config["fence_contract_sha256"],
            "SQL writer fence scope differs",
        )
        if getattr(self, "_audit_trigger", None) == "gm_v3_capture":
            _fence(db, plan["spec"], self.config["fence_owner_oid"], audit_trigger="gm_v3_capture")
        else:
            _fence(db, plan["spec"], self.config["fence_owner_oid"])
        need(
            _catalog(db) == self.config["source_catalog_sha256"],
            "Source schema/trigger catalog changed",
        )
        _live_rows(db, candidate, self.adapter, self.checkpoint)
        previous = self._prepared["backup"]
        current = self.ledger.get(previous["candidate"])
        need(current == previous, "Preflight database backup receipt changed")
        backup.require_current(
            [current],
            source=candidate.source,
            environment=candidate.environment,
            destination=self.store.identity,
            now=now,
            schema_scope=lambda: backup_scope.capture_scope(db, checkpoint=self.checkpoint),
        )
        self._snapshot_lock(receipt["snapshot"], current["snapshot"])
        _restore(self.restore_engine, self.config, candidate, receipt, self.checkpoint)
        self.checkpoint()
        need(time.monotonic() < self._deadline, "SQL source transaction deadline expired")
        return {
            "candidate": candidate.identity,
            "transaction_id": db.execute(text("SELECT txid_current()::text")).scalar_one(),
            "fence_key": lock_key,
            "proof_contract_sha256": self.evidence_sha256,
            "fence_contract_sha256": permit["fence_contract_sha256"],
            "checks": {name: True for name in sql_deletion.REQUIRED_PROOFS},
        }


_TOKEN = object()



class ProductionGuardV3(ProductionGuard):
    """V3 proof guard; no deletion until real Source audit restore attestation exists."""

    _audit_trigger = "gm_v3_capture"

    def prepare(self, candidate, receipt, now, permit):
        from .sql_deletion_v3 import require_native_restore_evidence

        evidence = require_native_restore_evidence(self, candidate, checkpoint=getattr(self, "checkpoint", None))
        result = super().prepare(candidate, receipt, now, permit)
        self._v3_restore = evidence
        return result

def build_guard(
    candidate,
    adapter,
    ledger,
    store,
    provider,
    restore_engine,
    *,
    evidence_path,
    evidence_sha256,
    checkpoint,
    _audit_v3=False,
):
    """Construct a root-owned, code-pinned guard; performs no deletion or DDL."""
    import httpx

    from .sql_runtime import ScopedReadStore, _read_only_request

    need(
        os.geteuid() == 0
        and type(adapter) is Source
        and type(ledger) is Ledger
        and type(store) is ScopedReadStore
        and type(store.transport) is StorageBox
        and type(provider) is SnapshotAPI
        and type(provider.client) is httpx.Client
        and provider.client.trust_env is False
        and provider.client.event_hooks.get("request") == [_read_only_request]
        and all(
            type(value) in (int, float) and 0 < value <= maximum
            for value, maximum in (
                (provider.client.timeout.connect, 3),
                (provider.client.timeout.read, 8),
                (provider.client.timeout.write, 8),
                (provider.client.timeout.pool, 8),
            )
        )
        and type(adapter.engine) is Engine
        and type(restore_engine) is Engine
        and adapter.engine.url.drivername in {"postgresql", "postgresql+psycopg2"}
        and not adapter.engine.url.query
        and callable(checkpoint),
        "Exact root SQL controller and production adapters required",
    )
    need(
        candidate.environment == "production"
        and candidate.source == "gateway"
        and candidate.dataset == "ingest_log"
        and candidate.kind == "logs",
        "Only production Gateway ingest-log pilot currently supported",
    )
    config = _private_json(evidence_path, evidence_sha256)
    keys = {
        "format",
        "candidate",
        "source_identity",
        "source_catalog_sha256",
        "audit_schema_sha256",
        "fence_contract_sha256",
        "fence_owner_oid",
        "restore_plan_path",
        "restore_plan_sha256",
        "restore_plan_file_sha256",
        "restore_target_sha256",
        "restore_session_id",
        "restore_evidence_sha256",
        "restore_batch_path",
        "restore_batch_sha256",
        "backup_candidate",
        "backup_manifest_sha256",
        "destination",
        "box_id",
    }
    if _audit_v3:
        keys |= {"audit_v3_epoch", "audit_v3_genesis_sha256", "audit_v3_backup_candidate", "audit_v3_backup_manifest_sha256", "audit_v3_restore_evidence_path", "audit_v3_restore_evidence_sha256"}
        need(type(config.get("audit_v3_epoch")) is str and re.fullmatch(r"[a-f0-9]{32}", config["audit_v3_epoch"])
             and all(_sha(config.get(k)) for k in ("audit_v3_genesis_sha256", "audit_v3_backup_candidate", "audit_v3_backup_manifest_sha256", "audit_v3_restore_evidence_sha256")),
             "Exact V3 audit backup and restore pins required")
    need(
        set(config) == keys
        and config["format"] == ("greenmind.sql-native-proof.v3" if _audit_v3 else "greenmind.sql-native-proof.v2")
        and config["candidate"] == candidate.identity
        and config["destination"] == store.identity
        and type(config["box_id"]) is int
        and config["box_id"] == provider.box_id
        and type(config["fence_owner_oid"]) is int
        and config["fence_owner_oid"] > 0
        and all(
            _sha(config[key])
            for key in (
                "source_catalog_sha256",
                "audit_schema_sha256",
                "fence_contract_sha256",
                "restore_plan_sha256",
                "restore_plan_file_sha256",
                "restore_target_sha256",
                "restore_session_id",
                "restore_evidence_sha256",
                "restore_batch_sha256",
                "backup_candidate",
                "backup_manifest_sha256",
            )
        ),
        "Exact native SQL proof pins required",
    )
    need(
        type(config["source_identity"]) is dict
        and set(config["source_identity"])
        == {
            "database",
            "database_oid",
            "role",
            "session_role",
            "role_oid",
            "restricted_role",
            "version",
            "replication_role",
            "isolation",
            "read_only",
            "search_path",
            "server_address",
            "server_port",
            "postmaster_start",
        },
        "Exact source identity pins required",
    )
    return (ProductionGuardV3 if _audit_v3 else ProductionGuard)(
        _TOKEN,
        config=config,
        evidence_path=evidence_path,
        evidence_sha256=evidence_sha256,
        candidate=candidate,
        adapter=adapter,
        ledger=ledger,
        store=store,
        provider=provider,
        restore_engine=restore_engine,
        checkpoint=checkpoint,
    )



def build_guard_v3(candidate, adapter, ledger, store, provider, restore_engine, *, evidence_path, evidence_sha256, checkpoint):
    """Construct the separately versioned guard; missing native restore stays HOLD."""
    return build_guard(candidate, adapter, ledger, store, provider, restore_engine,
                       evidence_path=evidence_path, evidence_sha256=evidence_sha256,
                       checkpoint=checkpoint, _audit_v3=True)

def source_audit_plan(writer_role):
    """Return reviewed additive SQL only; this function opens no connection."""
    from .sql_deletion_audit import ddl_plan

    return ddl_plan(writer_role)
