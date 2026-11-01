"""Native guard contract tests with fake query responses; no live SQL or DDL."""

from types import SimpleNamespace

import pytest

from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import sql_deletion as sql
from app.tiered_archive import sql_deletion_guard as guard
from tests.test_sql_deletion import setup_job


def test_guard_factory_does_not_accept_arbitrary_callback(tmp_path, monkeypatch):
    candidate, adapter, ledger, kwargs, live, _ = setup_job(tmp_path)
    monkeypatch.setattr(
        sql, "_production_guard", lambda value: type(value) is guard.ProductionGuard
    )
    with pytest.raises(ArchiveBlocked, match="Native SQL proof guard"):
        sql.evict(candidate, adapter, ledger, **kwargs)
    assert len(live) == 2
    ledger.close()


def test_fence_ddl_is_fixed_scope_and_generation_has_no_side_effects(tmp_path):
    candidate, _, ledger, _, _, _ = setup_job(tmp_path)
    plan = guard.fence_plan(candidate, sql._lock_key(candidate))
    assert plan == guard.fence_plan(candidate, sql._lock_key(candidate))
    assert plan["ddl"].count("CREATE TRIGGER") == 6
    assert plan["ddl"].count("ENABLE ALWAYS TRIGGER") == 6
    assert "DROP " not in plan["ddl"] and "TRUNCATE" in plan["ddl"]
    assert "TG_OP = 'INSERT'" in plan["spec"]["body"]
    assert plan["spec"]["tables"] == ["ingest_log", "gateway", "zone"]
    ledger.close()


@pytest.mark.parametrize(
    "fault", [None, "body", "disabled", "inheritance", "cascade", "extra_trigger"]
)
def test_fence_checks_real_catalog_shape_and_rejects_mutation(tmp_path, fault):
    candidate, _, ledger, _, _, _ = setup_job(tmp_path)
    spec = guard.fence_plan(candidate, sql._lock_key(candidate))["spec"]

    class Rows:
        def __init__(self, rows):
            self.rows = rows

        def mappings(self):
            return self

        def all(self):
            return self.rows

        def one_or_none(self):
            return self.rows[0] if self.rows else None

    class DB:
        def execute(self, query, params=None):
            query = str(query)
            if "FROM pg_proc p" in query:
                return Rows(
                    [
                        {
                            "body": "tampered" if fault == "body" else spec["body"],
                            "owner": 55,
                            "security_definer": False,
                            "volatility": "v",
                            "config": ["search_path=pg_catalog"],
                        }
                    ]
                )
            if "SELECT EXISTS(SELECT 1 FROM pg_inherits" in query:
                return SimpleNamespace(scalar_one=lambda: fault == "inheritance")
            if "FROM pg_trigger t" in query:

                def trigger(name, kind):
                    return {
                        "tgname": name,
                        "tgtype": kind,
                        "tgenabled": "O" if fault == "disabled" else "A",
                        "tgnargs": 0,
                        "unconditional": True,
                        "unconstrained": True,
                        "exact_function": True,
                        "attributes": "",
                    }

                rows = [trigger(spec["row_trigger"], 31), trigger(spec["truncate_trigger"], 34)]
                if params["table"] == "public.ingest_log":
                    row_audit = trigger("gm_sql_capture_deleted_row", 9)
                    row_audit["exact_function"] = False
                    rows.append(row_audit)
                if fault == "extra_trigger" and params["table"] == "public.ingest_log":
                    rows.append(trigger("foreign_trigger", 31))
                return Rows(rows)
            if "FROM pg_constraint WHERE" in query:
                return SimpleNamespace(scalar_one=lambda: 1 if fault == "cascade" else 0)
            if "FROM pg_class c WHERE" in query:
                return SimpleNamespace(one=lambda: ("r", False, False, False))
            raise AssertionError(query)

    if fault is None:
        guard._fence(DB(), spec, 55)
    else:
        with pytest.raises(ArchiveBlocked):
            guard._fence(DB(), spec, 55)
    ledger.close()


def test_full_scope_detects_unarchived_new_rows(tmp_path):
    candidate, adapter, ledger, _, _, _ = setup_job(tmp_path)

    def live(item):
        return {
            **item["body"]["row"],
            "_archive_org": item["organization"],
            "_archive_sensor": item["sensor"],
        }

    class DB:
        def __init__(self, rows):
            self.rows = rows

        def execute(self, _query, _params):
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: self.rows))

    rows = [live(item) for item in adapter.expected]
    guard._live_rows(DB(rows), candidate, adapter, lambda: None)
    with pytest.raises(ArchiveBlocked, match="partition changed"):
        guard._live_rows(DB(rows + [rows[0]]), candidate, adapter, lambda: None)
    foreign = [dict(rows[0], _archive_org="foreign"), rows[1]]
    with pytest.raises(ArchiveBlocked, match="tenant"):
        guard._live_rows(DB(foreign), candidate, adapter, lambda: None)
    ledger.close()


def test_full_factory_preflight_and_per_dml_guard_with_explicit_doubles(tmp_path, monkeypatch):
    from dataclasses import replace

    import httpx
    from sqlalchemy import create_engine

    from app.raw_archive.snapshot_api import SnapshotAPI
    from app.raw_archive.storage import StorageBox
    from app.tiered_archive import schema_provider
    from app.tiered_archive.sql_runtime import ScopedReadStore, _read_only_request

    original, adapter, ledger, _, _, _ = setup_job(tmp_path)
    candidate = replace(original, environment="production")
    adapter.environment = "production"
    adapter.engine = create_engine("postgresql+psycopg2://reader:x@127.0.0.1:5432/plantdb")
    lock_key = sql._lock_key(candidate)
    fence = guard.fence_plan(candidate, lock_key)
    snapshot = {
        "provider_id": 7,
        "box_id": 5,
        "name": "snap",
        "created_at": "2026-11-01T00:00:00+00:00",
        "checked_at": "2026-11-01T00:00:00+00:00",
    }
    transport = object.__new__(StorageBox)
    transport.user, transport.host, transport.port, transport.root = "u123", "host", 23, "archive"
    store = ScopedReadStore(transport)
    receipt = {
        "candidate": candidate.identity,
        "destination": store.identity,
        "snapshot": snapshot,
        "manifest": {"sha256": "e" * 64},
        "state": "verified",
    }
    backup_receipt = {
        "candidate": "b" * 64,
        "manifest": {"sha256": "c" * 64},
        "snapshot": snapshot,
        "files": [{}],
    }
    monkeypatch.setattr(
        ledger,
        "get",
        lambda identity: receipt if identity == candidate.identity else backup_receipt,
    )
    monkeypatch.setattr(ledger, "scope", lambda *_args: [backup_receipt])
    client = httpx.Client(
        trust_env=False,
        timeout=httpx.Timeout(8, connect=3),
        event_hooks={"request": [_read_only_request]},
    )
    provider = SnapshotAPI(client, 5, "u123", "u123.your-storagebox.de")
    engine = create_engine("postgresql+psycopg2://restore:x@127.0.0.1:5433/gm_restore_test")
    source_identity = {
        "database": "plantdb",
        "database_oid": 1,
        "role": "reader",
        "session_role": "reader",
        "role_oid": 2,
        "restricted_role": True,
        "version": 150010,
        "replication_role": "origin",
        "isolation": "read committed",
        "read_only": "off",
        "search_path": ["pg_catalog", "public"],
        "server_address": "127.0.0.1/32",
        "server_port": 5432,
        "postmaster_start": "pinned",
    }
    config = {
        "format": "greenmind.sql-native-proof.v2",
        "candidate": candidate.identity,
        "source_identity": source_identity,
        "source_catalog_sha256": "a" * 64,
        "audit_schema_sha256": "f" * 64,
        "fence_contract_sha256": fence["fence_contract_sha256"],
        "fence_owner_oid": 5,
        "restore_plan_path": "/pinned-plan",
        "restore_plan_sha256": "1" * 64,
        "restore_plan_file_sha256": "2" * 64,
        "restore_target_sha256": "3" * 64,
        "restore_session_id": "4" * 64,
        "restore_evidence_sha256": "5" * 64,
        "restore_batch_path": "/pinned-batch",
        "restore_batch_sha256": "6" * 64,
        "backup_candidate": "b" * 64,
        "backup_manifest_sha256": "c" * 64,
        "destination": store.identity,
        "box_id": 5,
    }
    trace = []
    monkeypatch.setattr(guard.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        guard, "_private_json", lambda path, _sha: {} if path == "/pinned-plan" else config
    )
    monkeypatch.setattr(guard.runtime, "runtime_sha256", lambda: "r" * 64)
    monkeypatch.setattr(guard.runtime, "bindings_sha256", lambda _store: "s" * 64)
    monkeypatch.setattr(
        guard, "check_loaded_function_code", lambda module, *_args: trace.append(module.__name__)
    )
    monkeypatch.setattr(guard.backup, "require_current", lambda *_args, **_kwargs: backup_receipt)
    monkeypatch.setattr(schema_provider, "current_descriptor", lambda *_: True)
    monkeypatch.setattr(
        guard.ProductionGuard, "_snapshot_lock", lambda *_: trace.append("snapshot-lock")
    )
    monkeypatch.setattr(guard, "_snapshot_reference", lambda *_: trace.append("remote-readback"))
    monkeypatch.setattr(guard, "_restore", lambda *_args, **_kwargs: trace.append("restore"))
    monkeypatch.setattr(
        guard.restore_session,
        "audit_restore_batch",
        lambda *_args, **_kwargs: {
            "phase": "verified",
            "replayed": True,
            "audited_pages": 1,
            "audited_rows": candidate.rows,
            "source_accesses": 0,
            "target_data_mutations": 0,
        },
    )
    monkeypatch.setattr(guard, "_identity", lambda _db: source_identity)
    monkeypatch.setattr(guard, "_fence", lambda *_: trace.append("fence"))
    monkeypatch.setattr(guard, "_catalog", lambda _db: "a" * 64)
    monkeypatch.setattr(guard, "_live_rows", lambda *_: trace.append("rows"))
    obj = guard.build_guard(
        candidate,
        adapter,
        ledger,
        store,
        provider,
        engine,
        evidence_path="/proof",
        evidence_sha256="d" * 64,
        checkpoint=lambda: None,
    )
    permit = {
        "proof_contract_sha256": "d" * 64,
        "fence_contract_sha256": fence["fence_contract_sha256"],
        "runtime_sha256": "r" * 64,
        "bindings_sha256": "s" * 64,
    }
    obj.prepare(
        candidate, receipt, __import__("datetime").datetime.now(__import__("datetime").UTC), permit
    )
    assert trace.count("remote-readback") == 2
    obj.begin_source()
    db = SimpleNamespace(execute=lambda _sql: SimpleNamespace(scalar_one=lambda: "42"))
    adapter.connection = db
    result = obj(
        candidate,
        receipt,
        __import__("datetime").datetime.now(__import__("datetime").UTC),
        db,
        lock_key,
        permit,
    )
    obj.check_deadline()
    assert result["transaction_id"] == "42" and all(result["checks"].values())
    assert "app.raw_archive.snapshot_api" in trace and trace.count("remote-readback") == 2
    assert "fence" in trace and "rows" in trace
    client.close()
    adapter.engine.dispose()
    engine.dispose()
    ledger.close()


def test_source_deadline_is_fixed_and_cannot_be_refreshed_by_check(monkeypatch):
    obj = object.__new__(guard.ProductionGuard)
    obj._prepared = {"expires": 150.0}
    obj._deadline = None
    moments = [100.0]
    monkeypatch.setattr(guard.time, "monotonic", lambda: moments[0])
    obj.begin_source()
    assert obj._deadline == 120.0
    moments[0] = 119.9
    obj.check_deadline()
    assert obj._deadline == 120.0
    with pytest.raises(ArchiveBlocked, match="preflight"):
        obj.begin_source()
    moments[0] = 120.0
    with pytest.raises(ArchiveBlocked, match="deadline"):
        obj.check_deadline()
