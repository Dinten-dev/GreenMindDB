"""Offline V3 scope contract: real SQLite/registry, synthetic Source and provider."""

import hashlib
import json
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.read_only_box import CatalogReadOnlyBox
from app.tiered_archive import codec, registry_backup, registry_v5, sql_audit_v3
from app.tiered_archive import sql_deletion as deletion
from app.tiered_archive import sql_deletion_guard as guards
from app.tiered_archive import sql_deletion_scope as scope
from app.tiered_archive import sql_deletion_v3 as v3
from app.tiered_archive.pipeline import candidate_json
from app.tiered_archive.storage import Store
from tests.test_sql_deletion import NOW, setup_job
from tests.test_tiered_archive import Box


@pytest.fixture
def context(tmp_path, monkeypatch):
    candidate, adapter, ledger, kwargs, _, _ = setup_job(tmp_path)
    monkeypatch.setenv("TIERED_ARCHIVE_SQL_V3_SCOPE_ENABLED", "true")
    for key in (
        "TIERED_ARCHIVE_DELETE_ENABLED",
        "TIERED_ARCHIVE_SQL_PRUNE_ENABLED",
        "TIERED_ARCHIVE_SQL_RETENTION_ACCEPTED",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(v3.os, "geteuid", lambda: 0)
    monkeypatch.setattr(v3, "bound_job_lock", lambda: None)
    monkeypatch.setattr(v3, "require_bound_source_lease", lambda: None)
    epoch, genesis, schema, snapshot = "1" * 32, "2" * 64, "3" * 64, "4" * 64
    manifest, cipher, plain = "5" * 64, "6" * 64, "7" * 64
    original = json.loads(kwargs["authorization_path"].read_bytes())
    original["format"] = "greenmind.sql-deletion.v3"
    original["audit"] = {
        "epoch": epoch,
        "genesis_sha256": genesis,
        "schema_sha256": schema,
        "slot": 1,
        "pk_sha256": codec.digest(
            [{"measurement_id": item["body"]["row"]["measurement_id"]} for item in adapter.expected]
        ),
        "source_row_sha256": ["8" * 64, "9" * 64],
    }
    raw = json.dumps(original, sort_keys=True).encode()
    kwargs["authorization_path"].write_bytes(raw)
    kwargs["authorization_sha256"] = hashlib.sha256(raw).hexdigest()
    current_receipt = ledger.get(candidate.identity)
    current_receipt["source"] = candidate_json(candidate)
    ledger.save(candidate, "verified", current_receipt, NOW)
    backup_candidate = replace(
        candidate,
        organization="00000000-0000-0000-0000-000000000000",
        dataset="database_backup",
        kind="features",
    )
    files = [
        {
            "backup_format": "schema-master-provider-sql-audit-v3",
            "audit_v3_epoch": epoch,
            "audit_v3_genesis_sha256": genesis,
            "audit_v3_schema_sha256": schema,
            "audit_v3_snapshot_sha256": snapshot,
            "audit_v3_plaintext_sha256": plain,
        },
        {},
        {},
        {},
        {},
        {"sha256": cipher, "plaintext_sha256": plain},
    ]
    ledger.save(
        backup_candidate,
        "published",
        {
            "source": candidate_json(backup_candidate),
            "manifest": {"sha256": manifest},
            "files": files,
            "destination": "local-test-storagebox",
        },
        NOW,
    )
    ledger.db.execute(registry_v5.DDL["sql_deletion_pilot"])
    ledger.db.execute(registry_v5.DDL["sql_audit_v3_backup"])
    ledger.db.execute(
        "INSERT INTO sql_audit_v3_backup VALUES(?,?,?,?,?)",
        (epoch, backup_candidate.identity, manifest, cipher, plain),
    )
    ledger.db.commit()
    box = Box(tmp_path / "registry-box")
    reference = registry_backup.backup(ledger, box, checkpoint=lambda: None)
    ro = object.__new__(CatalogReadOnlyBox)
    ro.identity = box.identity
    verifier = Store(ro)
    monkeypatch.setattr(
        Store, "download", lambda self, key, path, size, **kw: box.download(key, path, size, **kw)
    )
    registry = scope.verify_registry_snapshot(
        ledger, verifier, reference, environment="staging", checkpoint=lambda: None
    )
    identity = {"database": "plantdb", "role": "writer", "role_oid": 43}
    guard = object.__new__(guards.ProductionGuardV3)
    guard.config = {
        "source_identity": identity,
        "audit_schema_sha256": schema,
        "audit_v3_epoch": epoch,
        "audit_v3_genesis_sha256": genesis,
        "audit_v3_backup_candidate": backup_candidate.identity,
        "audit_v3_backup_manifest_sha256": manifest,
        "audit_v3_restore_evidence_sha256": "a" * 64,
    }
    empty_history = {
        "approvals": 0,
        "commits": 0,
        "history_sha256": codec.digest({"approvals": [], "events": [], "rows": []}),
    }

    def prepare(self, *_):
        self._v3_restore = {
            "candidate": backup_candidate.identity,
            "gateway_manifest_sha256": manifest,
            "audit_plaintext_sha256": plain,
            "snapshot_sha256": snapshot,
            "history": dict(empty_history),
            "native_schema_grants_rows_verified": True,
        }

    def guarded(self, c, _receipt, _now, _db, lock, accepted):
        return {
            "candidate": c.identity,
            "transaction_id": "42",
            "fence_key": lock,
            "proof_contract_sha256": accepted["proof_contract_sha256"],
            "fence_contract_sha256": accepted["fence_contract_sha256"],
            "checks": dict.fromkeys(deletion.REQUIRED_PROOFS, True),
        }

    monkeypatch.setattr(guards.ProductionGuardV3, "prepare", prepare)
    monkeypatch.setattr(guards.ProductionGuardV3, "__call__", guarded)
    monkeypatch.setattr(guards.ProductionGuardV3, "begin_source", lambda self: None)
    monkeypatch.setattr(guards.ProductionGuardV3, "check_deadline", lambda self: None)
    monkeypatch.setattr(guards, "_identity", lambda _db: identity)
    monkeypatch.setattr(sql_audit_v3, "schema_sha256", lambda _db: schema)
    monkeypatch.setattr(sql_audit_v3, "inspect_writer", lambda *_args: None)

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one(self):
            return self.value

        def one_or_none(self):
            return self.value

        def all(self):
            return self.value

    class Connection:
        dialect = SimpleNamespace(name="postgresql")
        unknown = 0
        approvals = []
        events = []
        rows = []

        def execute(self, statement, _params=None):
            query = str(statement)
            if "pg_try_advisory_xact_lock" in query:
                return Result(True)
            if "txid_current" in query:
                return Result("42")
            if "gm_sql_v3_generation" in query:
                return Result((epoch, genesis, 43))
            if "gm_sql_v3_unknown" in query:
                return Result(self.unknown)
            if "FROM public.gm_sql_v3_approval" in query:
                return Result(self.approvals)
            if "FROM public.gm_sql_v3_event" in query:
                return Result(self.events)
            if "FROM public.gm_sql_v3_row" in query:
                return Result(self.rows)
            raise AssertionError(query)

    source_db = Connection()

    @contextmanager
    def exclusive(_candidate):
        adapter.connection = source_db
        try:
            yield
        finally:
            adapter.connection = None

    adapter.exclusive = exclusive
    kwargs["proof_guard"] = guard
    kwargs["registry_evidence"] = registry
    yield SimpleNamespace(
        candidate=candidate,
        adapter=adapter,
        ledger=ledger,
        kwargs=kwargs,
        source_db=source_db,
        registry_box=box,
        verifier=verifier,
        epoch=epoch,
        genesis=genesis,
        empty_history=empty_history,
        backup_candidate=backup_candidate,
    )
    ledger.close()


def refresh_registry(context):
    reference = registry_backup.backup(
        context.ledger, context.registry_box, checkpoint=lambda: None
    )
    context.kwargs["registry_evidence"] = scope.verify_registry_snapshot(
        context.ledger, context.verifier, reference, environment="staging", checkpoint=lambda: None
    )


def test_bootstrap_and_restored_scope_reconcile_preserve_budget(context, tmp_path):
    result = v3.scope_transition(
        context.candidate, context.adapter, context.ledger, **context.kwargs, bootstrap=True
    )
    assert (
        result["status"] == "SQL_AUDIT_V3_SCOPE_CURRENT" and result["deletion_performed"] is False
    )
    assert context.ledger.db.execute("SELECT state FROM sql_audit_v3_scope").fetchone() == (
        "current",
    )
    assert context.ledger.db.execute("SELECT count(*) FROM sql_deletion_pilot").fetchone() == (0,)
    reference = registry_backup.backup(
        context.ledger, context.registry_box, checkpoint=lambda: None
    )
    from app.tiered_archive.ledger import Ledger

    restored = Ledger(tmp_path / "restored-v3")
    try:
        registry_backup.restore(
            reference,
            restored,
            context.verifier,
            checkpoint=lambda: None,
            expected_environment="staging",
        )
        assert restored.db.execute("SELECT state FROM sql_audit_v3_scope").fetchone() == (
            "needs_reconcile",
        )
        assert (
            restored.db.execute("SELECT body_sha256 FROM sql_audit_v3_scope_proof").fetchone()[0]
            == result["proof_sha256"]
        )
    finally:
        restored.close()
    with context.ledger.db:
        context.ledger.db.execute("UPDATE sql_audit_v3_scope SET state='needs_reconcile'")
    refresh_registry(context)
    result2 = v3.scope_transition(
        context.candidate, context.adapter, context.ledger, **context.kwargs, bootstrap=False
    )
    assert (
        result2["status"] == "SQL_AUDIT_V3_SCOPE_CURRENT" and not result2["pilot_budget_consumed"]
    )
    assert context.ledger.db.execute("SELECT state FROM sql_audit_v3_scope").fetchone() == (
        "current",
    )


def test_unknown_source_delete_and_stale_registry_hold(context):
    context.source_db.unknown = 1
    with pytest.raises(ArchiveBlocked, match="Unknown Source DELETE"):
        v3.scope_transition(
            context.candidate, context.adapter, context.ledger, **context.kwargs, bootstrap=True
        )
    assert not scope._table(context.ledger.db, "sql_audit_v3_scope")
    context.source_db.unknown = 0
    with context.ledger.db:
        context.ledger.db.execute(
            "INSERT INTO sql_deletion_pilot VALUES(?,?,?)",
            ("a" * 32, context.candidate.identity, "b" * 64),
        )
    with pytest.raises(ArchiveBlocked, match="registry restore evidence"):
        v3.scope_transition(
            context.candidate, context.adapter, context.ledger, **context.kwargs, bootstrap=True
        )
    assert not scope._table(context.ledger.db, "sql_audit_v3_scope")


def test_missing_proof_replay_and_tampering_hold(context):
    v3.scope_transition(
        context.candidate, context.adapter, context.ledger, **context.kwargs, bootstrap=True
    )
    refresh_registry(context)
    with pytest.raises(ArchiveBlocked, match="known empty origin"):
        v3.scope_transition(
            context.candidate, context.adapter, context.ledger, **context.kwargs, bootstrap=True
        )
    with context.ledger.db:
        context.ledger.db.execute("UPDATE sql_audit_v3_scope_proof SET body_sha256=?", ("0" * 64,))
    with pytest.raises(ArchiveBlocked, match="scope proof"):
        v3.scope_transition(
            context.candidate, context.adapter, context.ledger, **context.kwargs, bootstrap=False
        )


def test_committed_pilot_reconcile_preserves_consumed_budget(context):
    v3.scope_transition(
        context.candidate, context.adapter, context.ledger, **context.kwargs, bootstrap=True
    )
    old = replace(context.candidate, content_sha256="f" * 64)
    pilot, auth = "b" * 32, "c" * 64
    historic = {
        "pilot_id": pilot,
        "candidate": old.identity,
        "runtime_sha256": "a" * 64,
        "bindings_sha256": "b" * 64,
        "proof_contract_sha256": "c" * 64,
        "fence_contract_sha256": "d" * 64,
    }
    body = v3.approval_body(historic, auth)
    hashes = ["8" * 64, "9" * 64]
    intent = {
        "pilot_id": pilot,
        "authorization_sha256": auth,
        "audit_v3_slot": 1,
        "audit_v3_epoch": context.epoch,
        "audit_v3_body": body,
        "source_row_sha256": hashes,
        **{
            key: historic[key]
            for key in (
                "runtime_sha256",
                "bindings_sha256",
                "proof_contract_sha256",
                "fence_contract_sha256",
            )
        },
    }
    data = {"source": candidate_json(old), "deletion_intent": intent}
    context.ledger.save(old, "published", data, NOW)
    context.ledger.save(old, "verified", data, NOW)
    context.ledger.save(old, "deleting", data, NOW)
    context.ledger.save(old, "evicted", data, NOW)
    with context.ledger.db:
        context.ledger.db.execute(
            "INSERT INTO sql_deletion_pilot VALUES(?,?,?)", (pilot, old.identity, auth)
        )
        context.ledger.db.execute("UPDATE sql_audit_v3_scope SET state='needs_reconcile'")
    context.source_db.approvals = [(1, context.epoch, old.identity, auth, body, hashes)]
    context.source_db.events = [(1, "123")]
    context.source_db.rows = [(1, item) for item in hashes]
    context.empty_history.update(
        approvals=1,
        commits=1,
        history_sha256=codec.digest(
            {
                "approvals": [[1, context.epoch, old.identity, auth, body, hashes]],
                "events": [[1, "123"]],
                "rows": [[1, item] for item in hashes],
            }
        ),
    )
    refresh_registry(context)
    result = v3.scope_transition(
        context.candidate, context.adapter, context.ledger, **context.kwargs, bootstrap=False
    )
    assert result["pilot_budget_consumed"] is True
    assert context.ledger.db.execute(
        "SELECT pilot_id,candidate,authorization_sha256 FROM sql_deletion_pilot"
    ).fetchall() == [(pilot, old.identity, auth)]
    assert context.ledger.get(old.identity)["state"] == "evicted"
    # No second pilot can reuse this restored scope.
    permit = v3.activation(
        context.kwargs["authorization_path"],
        context.kwargs["authorization_sha256"],
        context.candidate,
        NOW,
    )
    with pytest.raises(ArchiveBlocked, match="budget already consumed"):
        v3._budget(
            context.ledger, context.candidate, permit, context.kwargs["authorization_sha256"]
        )


def test_source_disconnect_after_local_commit_downgrades_scope(context):
    original = context.adapter.exclusive

    @contextmanager
    def disconnect(candidate):
        with original(candidate):
            yield
            raise OSError("synthetic source disconnect")

    context.adapter.exclusive = disconnect
    with pytest.raises(OSError, match="synthetic source disconnect"):
        v3.scope_transition(
            context.candidate, context.adapter, context.ledger, **context.kwargs, bootstrap=True
        )
    assert context.ledger.db.execute("SELECT state FROM sql_audit_v3_scope").fetchone() == (
        "needs_reconcile",
    )
    assert context.ledger.db.execute(
        "SELECT count(*) FROM sql_audit_v3_scope_proof"
    ).fetchone() == (1,)


def test_new_actual_backup_anchor_forces_reconcile_and_preserves_prior(context, monkeypatch):
    from app.raw_archive import coordination, remote_lease
    from app.tiered_archive import sql_audit_v3_backup as backup

    v3.scope_transition(
        context.candidate, context.adapter, context.ledger, **context.kwargs, bootstrap=True
    )
    old_row = context.ledger.db.execute("SELECT * FROM sql_audit_v3_backup").fetchone()
    later = replace(context.backup_candidate, content_sha256="e" * 64)
    files = [
        dict(context.ledger.get(context.backup_candidate.identity)["files"][0]),
        {},
        {},
        {},
        {},
        {"sha256": "a" * 64, "plaintext_sha256": "b" * 64},
    ]
    files[0]["audit_v3_plaintext_sha256"] = "b" * 64
    context.ledger.save(
        later,
        "published",
        {
            "source": candidate_json(later),
            "manifest": {"sha256": "c" * 64},
            "files": files,
            "destination": context.verifier.identity,
        },
        NOW,
    )
    monkeypatch.setattr(coordination, "bound_job_lock", lambda: None)
    monkeypatch.setattr(remote_lease, "require_bound_source_lease", lambda: None)
    monkeypatch.setattr(
        backup,
        "verify_gateway_bundle",
        lambda *_args, **_kw: {
            "candidate": later.identity,
            "gateway_manifest_sha256": "c" * 64,
            "audit_plaintext_sha256": "b" * 64,
            "unknown": False,
            "restore_verified": False,
        },
    )
    result = backup.record_gateway_readback(
        later, context.ledger, context.verifier, b"k" * 32, checkpoint=lambda: None
    )
    assert result["registry_reference_recorded"] is True
    assert context.ledger.db.execute("SELECT state FROM sql_audit_v3_scope").fetchone() == (
        "needs_reconcile",
    )
    rows = context.ledger.db.execute(
        "SELECT * FROM sql_audit_v3_backup_history ORDER BY candidate"
    ).fetchall()
    assert len(rows) == 2 and old_row in rows
    assert context.ledger.db.execute("SELECT candidate FROM sql_audit_v3_backup").fetchone() == (
        later.identity,
    )
    # Previous scope proof remains anchored to immutable history, not discarded.
    proof = context.ledger.db.execute("SELECT * FROM sql_audit_v3_scope_proof").fetchone()
    registry_v5.validate("sql_audit_v3_scope_proof", list(proof), "staging", context.ledger)
