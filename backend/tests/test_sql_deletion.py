"""Synthetic SQL-pilot tests. No production database or server access."""

import hashlib
import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import Column, MetaData, String, Table

from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import codec
from app.tiered_archive import sql_deletion as sql
from app.tiered_archive.ledger import Ledger
from app.tiered_archive.policy import POLICY_SHA256, Candidate
from app.tiered_archive.source import DATASETS, Source, row_identity

NOW = datetime(2026, 11, 1, tzinfo=UTC)
ORG = "11111111-1111-4111-8111-111111111111"


@pytest.fixture(autouse=True)
def enable_sql_for_local_test(monkeypatch):
    for name in (
        "TIERED_ARCHIVE_DELETE_ENABLED",
        "TIERED_ARCHIVE_SQL_PRUNE_ENABLED",
        "TIERED_ARCHIVE_SQL_RETENTION_ACCEPTED",
    ):
        monkeypatch.setenv(name, "true")
    # These orchestration tests use explicit fakes, never a production guard.
    monkeypatch.setattr(sql, "_production_guard", callable)
    monkeypatch.setattr(sql, "_prepare_guard", lambda *_args: None)
    monkeypatch.setattr(sql, "_begin_guard", lambda *_args: None)
    monkeypatch.setattr(sql, "_guard_deadline", lambda *_args: None)
    from app.tiered_archive import sql_deletion_audit, sql_deletion_scope

    monkeypatch.setattr(sql_deletion_scope, "current_epoch", lambda *_args: {})
    monkeypatch.setattr(sql, "_audit_begin", lambda *_args: {"epoch": "1" * 32})
    monkeypatch.setattr(sql, "_audit_commit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(sql_deletion_audit, "insert", lambda *_args: "2" * 64)
    monkeypatch.setattr(
        sql_deletion_audit, "native_row_hashes", lambda *_args: ["a" * 64, "b" * 64]
    )
    monkeypatch.setattr(sql_deletion_audit, "arm_row_trigger", lambda *_args: None)
    monkeypatch.setattr(sql_deletion_audit, "verify_row_trigger", lambda *_args: None)


def setup_job(tmp_path, kind="logs", dataset="ingest_log"):
    spec = DATASETS[dataset]
    rows = []
    for value in ("a", "b"):
        row = {key: key + "-" + value for key in spec.keys}
        rows.append(
            {
                "identity": row_identity(spec, row),
                "organization": ORG,
                "sensor": "sensor",
                "time_us": 1,
                "body": {"row": row, "context": {}},
            }
        )
    rows.sort(key=lambda r: (r["time_us"], r["identity"]))
    h = hashlib.sha256()
    for item in rows:
        value = codec.encode(item)
        h.update(len(value).to_bytes(8, "big"))
        h.update(value)
    candidate = Candidate(
        "staging",
        "gateway",
        dataset,
        ORG,
        NOW - timedelta(days=150),
        NOW - timedelta(days=149),
        h.hexdigest(),
        2,
        kind,
        NOW - timedelta(days=148),
        complete=True,
        features_verified=True,
        projections_verified=True,
        dependencies_complete=True,
    )
    ledger = Ledger(tmp_path / "state")
    ledger.db.execute(
        "CREATE TABLE sql_deletion_scope (id INTEGER PRIMARY KEY, state TEXT NOT NULL)"
    )
    ledger.db.execute("INSERT INTO sql_deletion_scope VALUES (1,'current')")
    ledger.db.commit()
    ledger.save(candidate, "published", {}, NOW - timedelta(days=10))
    receipt = {
        "verified_at": NOW.isoformat(),
        "snapshot": {
            "created_at": NOW.isoformat(),
            "checked_at": NOW.isoformat(),
            "provider_id": 1,
            "box_id": 2,
        },
    }
    ledger.save(candidate, "verified", receipt, NOW - timedelta(days=8))
    contract, fence = "c" * 64, "d" * 64
    permit = {
        "format": "greenmind.sql-deletion.v2",
        "policy": POLICY_SHA256,
        "sql_policy": sql.SQL_POLICY_SHA256,
        "mode": "pilot",
        "pilot_id": "1" * 32,
        "environment": candidate.environment,
        "candidate": candidate.identity,
        "runtime_sha256": "a" * 64,
        "bindings_sha256": "b" * 64,
        "proof_contract_sha256": contract,
        "fence_contract_sha256": fence,
        "authorized_at": (NOW - timedelta(days=1)).isoformat(),
        "expires_at": (NOW + timedelta(days=1)).isoformat(),
    }
    permit["acceptance"] = {
        key: permit[key]
        for key in (
            "sql_policy",
            "candidate",
            "runtime_sha256",
            "bindings_sha256",
            "proof_contract_sha256",
            "fence_contract_sha256",
        )
    }
    permit["acceptance"].update(
        deployed_at=(NOW - timedelta(days=3)).isoformat(),
        observation={
            "started_at": (NOW - timedelta(days=2)).isoformat(),
            "finished_at": (NOW - timedelta(days=1)).isoformat(),
            "maximum_gap_seconds": 300,
            "samples": 288,
            "errors": 0,
            "progress": {"gateway": True, "direct": True},
        },
    )
    file = tmp_path / "sql-approval.json"
    raw = json.dumps(permit, sort_keys=True).encode()
    file.write_bytes(raw)
    file.chmod(0o600)
    source = object.__new__(Source)
    source.source, source.environment, source.expected = "gateway", "staging", rows
    source.connection = None
    live = set(row["body"]["row"][spec.keys[0]] for row in rows)
    events = []

    class Connection:
        dialect = SimpleNamespace(name="postgresql")

        def execute(self, statement, params=None):
            command = str(statement)
            if "pg_try_advisory_xact_lock" in command:
                events.append("lock")
                return SimpleNamespace(scalar_one=lambda: True)
            if "txid_current" in command:
                return SimpleNamespace(scalar_one=lambda: "42")
            if command.startswith("DELETE"):
                events.append("delete")
                value = adapter.expected[0]["body"]["row"][spec.keys[0]]
                live.remove(value)
                return SimpleNamespace(rowcount=1)
            raise AssertionError(command)

    connection = Connection()

    @contextmanager
    def exclusive(_candidate):
        before = set(live)
        source.connection = connection
        try:
            yield
        except BaseException:
            live.clear()
            live.update(before)
            raise
        finally:
            source.connection = None

    source.exclusive = exclusive
    source.inspect = lambda _candidate: candidate
    source.absent = lambda _candidate: not live
    source.table = lambda name: Table(
        name, MetaData(), *(Column(key, String, primary_key=True) for key in spec.keys)
    )
    adapter = source

    def guard(c, receipt, now, db, lock_key, accepted):
        events.append("guard")
        return {
            "candidate": c.identity,
            "transaction_id": "42",
            "fence_key": lock_key,
            "proof_contract_sha256": contract,
            "fence_contract_sha256": fence,
            "checks": {name: True for name in sql.REQUIRED_PROOFS},
        }

    kwargs = dict(
        authorization_path=file,
        authorization_sha256=hashlib.sha256(raw).hexdigest(),
        runtime_sha256="a" * 64,
        bindings_sha256="b" * 64,
        checkpoint=lambda: None,
        clock=lambda: NOW,
        proof_guard=guard,
    )
    return candidate, adapter, ledger, kwargs, live, events


def test_exact_rows_and_final_guard_before_each_dml(tmp_path):
    candidate, adapter, ledger, kwargs, live, events = setup_job(tmp_path)
    result = sql.evict(candidate, adapter, ledger, **kwargs)
    assert result["state"] == "evicted" and not live
    assert events.count("delete") == 2 and events.count("guard") >= 3
    assert all(
        events[index - 1] == "guard" for index, item in enumerate(events) if item == "delete"
    )
    ledger.close()


def test_missing_fence_or_proof_blocks_before_intent(tmp_path):
    candidate, adapter, ledger, kwargs, live, events = setup_job(tmp_path)
    kwargs["proof_guard"] = lambda *_: None
    with pytest.raises(ArchiveBlocked, match="proof guard"):
        sql.evict(candidate, adapter, ledger, **kwargs)
    assert len(live) == 2 and ledger.get(candidate.identity)["state"] == "verified"
    assert "delete" not in events
    ledger.close()


def test_partial_failure_rolls_back_and_resume_same_pilot(tmp_path):
    candidate, adapter, ledger, kwargs, live, events = setup_job(tmp_path)
    original = kwargs["proof_guard"]
    calls = [0]

    def interrupted(*args):
        calls[0] += 1
        if calls[0] == 3:
            raise RuntimeError("interrupted")
        return original(*args)

    kwargs["proof_guard"] = interrupted
    with pytest.raises(RuntimeError, match="interrupted"):
        sql.evict(candidate, adapter, ledger, **kwargs)
    assert len(live) == 2 and ledger.get(candidate.identity)["state"] == "deleting"
    kwargs["proof_guard"] = original
    assert sql.evict(candidate, adapter, ledger, **kwargs)["state"] == "evicted" and not live
    assert ledger.db.execute("SELECT COUNT(*) FROM sql_deletion_pilot").fetchone()[0] == 1
    ledger.close()


def test_commit_before_final_ledger_recovers_without_repeat_delete(tmp_path):
    candidate, adapter, ledger, kwargs, live, events = setup_job(tmp_path)
    original = ledger.save

    def fail_final(candidate, state, data, now=None):
        if state == "evicted":
            raise RuntimeError("journal interrupted")
        return original(candidate, state, data, now)

    ledger.save = fail_final
    with pytest.raises(RuntimeError, match="journal"):
        sql.evict(candidate, adapter, ledger, **kwargs)
    assert not live and ledger.get(candidate.identity)["state"] == "deleting"
    count = events.count("delete")
    ledger.save = original
    assert sql.evict(candidate, adapter, ledger, **kwargs)["state"] == "evicted"
    assert events.count("delete") == count
    ledger.close()


def test_pilot_budget_persists_across_candidates(tmp_path):
    from dataclasses import replace

    candidate, adapter, ledger, kwargs, live, events = setup_job(tmp_path)
    permit = sql.activation(
        kwargs["authorization_path"], kwargs["authorization_sha256"], candidate, NOW
    )
    sql._budget(ledger, permit, candidate, kwargs["authorization_sha256"])
    with pytest.raises(ArchiveBlocked, match="budget"):
        sql._budget(
            ledger,
            permit,
            replace(candidate, content_sha256="f" * 64),
            kwargs["authorization_sha256"],
        )
    assert len(live) == 2
    ledger.close()


def test_unsupported_data_and_missing_guard_are_always_excluded(tmp_path):
    from dataclasses import replace

    candidate, adapter, ledger, kwargs, live, _ = setup_job(tmp_path)
    kwargs["proof_guard"] = None
    with pytest.raises(ArchiveBlocked, match="proof guard"):
        sql.evict(candidate, adapter, ledger, **kwargs)
    for changed in (
        replace(candidate, dataset="direct_chunk", kind="direct_payload", source="direct"),
        replace(candidate, dataset="sensor_reading", kind="raw_readings"),
        replace(candidate, dataset="direct_revision", kind="features", source="direct"),
    ):
        with pytest.raises(ArchiveBlocked):
            sql._scope(changed, adapter, lambda: None)
    assert len(live) == 2
    ledger.close()


def test_changed_authorization_or_live_source_digest_blocks(tmp_path):
    candidate, adapter, ledger, kwargs, live, _ = setup_job(tmp_path)
    adapter.expected[0]["body"]["row"]["measurement_id"] = "changed"
    with pytest.raises(ArchiveBlocked):
        sql.evict(candidate, adapter, ledger, **kwargs)
    assert len(live) == 2
    ledger.close()


def test_sql_policies_wait_required_local_and_verified_reserves(tmp_path):
    from dataclasses import replace

    from app.tiered_archive.policy import eligibility

    candidate, _, ledger, _, _, _ = setup_job(tmp_path)
    receipt = ledger.get(candidate.identity)
    assert eligibility(candidate, receipt, NOW) <= NOW
    for kind, required in (("features", 90), ("dashboard", 90), ("logs", 30)):
        recent = replace(candidate, kind=kind, end=NOW - timedelta(days=required - 1))
        adjusted = {**receipt, "candidate": recent.identity}
        with pytest.raises(ArchiveBlocked, match="reserve"):
            eligibility(recent, adjusted, NOW)
    newer = {**receipt, "first_verified_at": (NOW - timedelta(days=6)).isoformat()}
    with pytest.raises(ArchiveBlocked, match="reserve"):
        eligibility(candidate, newer, NOW)
    ledger.close()


def test_flags_disabled_keep_all_source_rows(tmp_path, monkeypatch):
    candidate, adapter, ledger, kwargs, live, events = setup_job(tmp_path)
    monkeypatch.setenv("TIERED_ARCHIVE_SQL_PRUNE_ENABLED", "false")
    with pytest.raises(ArchiveBlocked, match="enablement"):
        sql.evict(candidate, adapter, ledger, **kwargs)
    assert len(live) == 2 and "delete" not in events
    ledger.close()


def test_dashboard_reference_and_each_delete_have_own_guard(tmp_path, monkeypatch):
    candidate, adapter, ledger, kwargs, live, events = setup_job(
        tmp_path, kind="dashboard", dataset="visual_reading"
    )

    def preserve(_db, _dataset, rows, _identity):
        assert len(rows) == 1
        events.append("preserve")

    monkeypatch.setattr(sql.gateway_references, "preserve", preserve)
    assert sql.evict(candidate, adapter, ledger, **kwargs)["state"] == "evicted"
    assert events.count("preserve") == events.count("delete") == 2
    assert all(
        events[i - 1] == "guard" for i, item in enumerate(events) if item in {"preserve", "delete"}
    )
    assert not live
    ledger.close()


def test_recovery_rejects_mismatched_deletion_intent(tmp_path):
    candidate, adapter, ledger, kwargs, live, events = setup_job(tmp_path)
    receipt = ledger.get(candidate.identity)
    ledger.save(candidate, "deleting", {**receipt, "deletion_intent": {"candidate": "wrong"}}, NOW)
    with pytest.raises(ArchiveBlocked, match="intent"):
        sql.evict(candidate, adapter, ledger, **kwargs)
    assert len(live) == 2 and "delete" not in events
    ledger.close()


def test_writer_fence_unavailable_blocks_before_delete(tmp_path):
    candidate, adapter, ledger, kwargs, live, events = setup_job(tmp_path)
    original_exclusive = adapter.exclusive

    @contextmanager
    def unavailable(candidate):
        with original_exclusive(candidate):
            original = adapter.connection.execute

            def execute(statement, params=None):
                if "pg_try_advisory_xact_lock" in str(statement):
                    return SimpleNamespace(scalar_one=lambda: False)
                return original(statement, params)

            adapter.connection.execute = execute
            yield

    adapter.exclusive = unavailable
    with pytest.raises(ArchiveBlocked, match="fence unavailable"):
        sql.evict(candidate, adapter, ledger, **kwargs)
    assert len(live) == 2 and "delete" not in events
    ledger.close()


def test_pause_during_transaction_rolls_back_first_delete(tmp_path, monkeypatch):
    candidate, adapter, ledger, kwargs, live, events = setup_job(tmp_path)
    original = kwargs["proof_guard"]

    def pause_after_first(*args):
        value = original(*args)
        if events.count("delete") == 1:
            monkeypatch.setenv("TIERED_ARCHIVE_SQL_PRUNE_ENABLED", "false")
        return value

    kwargs["proof_guard"] = pause_after_first
    with pytest.raises(ArchiveBlocked, match="enablement"):
        sql.evict(candidate, adapter, ledger, **kwargs)
    assert len(live) == 2
    assert ledger.get(candidate.identity)["state"] == "deleting"
    ledger.close()


def test_guard_checks_empty_scope_after_delete_and_during_reconciliation(tmp_path):
    candidate, adapter, ledger, kwargs, live, _ = setup_job(tmp_path)
    original_guard = kwargs["proof_guard"]
    observed = []

    def record(*args):
        observed.append(len(adapter.expected))
        return original_guard(*args)

    kwargs["proof_guard"] = record
    original_save = ledger.save

    def fail_final(candidate, state, data, now=None):
        if state == "evicted":
            raise RuntimeError("interrupted journal")
        return original_save(candidate, state, data, now)

    ledger.save = fail_final
    with pytest.raises(RuntimeError):
        sql.evict(candidate, adapter, ledger, **kwargs)
    assert not live and observed[-1] == 0
    observed.clear()
    ledger.save = original_save
    assert sql.evict(candidate, adapter, ledger, **kwargs)["state"] == "evicted"
    assert observed == [0]
    ledger.close()


def test_source_audit_rolls_back_with_rows_and_recovers_committed_intent(tmp_path, monkeypatch):
    from app.tiered_archive import sql_deletion_audit

    candidate, adapter, ledger, kwargs, live, events = setup_job(tmp_path)
    original_exclusive = adapter.exclusive
    audit_records = []
    source_tx = [False]

    @contextmanager
    def transactional(candidate):
        snapshot = list(audit_records)
        source_tx[0] = True
        try:
            with original_exclusive(candidate):
                yield
        except BaseException:
            audit_records[:] = snapshot
            raise
        finally:
            source_tx[0] = False

    adapter.exclusive = transactional
    audit_body = {"epoch": "a" * 32, "candidate": candidate.identity}
    monkeypatch.setattr(sql, "_audit_begin", lambda *_: audit_body)

    def check_audit(_db, body, *, already_absent):
        assert source_tx[0]
        if already_absent:
            if audit_records != [body]:
                raise ArchiveBlocked("Source audit absent")
        elif audit_records:
            raise ArchiveBlocked("Source audit already committed")

    monkeypatch.setattr(sql, "_audit_commit", check_audit)
    fail = [True]

    def insert(_db, kind, body):
        assert source_tx[0] and kind == "commit"
        audit_records.append(body.copy())
        if fail[0]:
            raise RuntimeError("source audit interrupted")
        return "f" * 64

    monkeypatch.setattr(sql_deletion_audit, "insert", insert)
    with pytest.raises(RuntimeError, match="source audit interrupted"):
        sql.evict(candidate, adapter, ledger, **kwargs)
    assert len(live) == 2 and audit_records == []
    assert ledger.get(candidate.identity)["state"] == "deleting"
    fail[0] = False
    original_save = ledger.save

    def interrupted_ledger(candidate, state, data, now=None):
        if state == "evicted":
            raise RuntimeError("after source commit")
        return original_save(candidate, state, data, now)

    ledger.save = interrupted_ledger
    with pytest.raises(RuntimeError, match="after source commit"):
        sql.evict(candidate, adapter, ledger, **kwargs)
    assert not live and audit_records == [audit_body]
    prior_deletes = events.count("delete")
    ledger.save = original_save
    assert sql.evict(candidate, adapter, ledger, **kwargs)["state"] == "evicted"
    assert events.count("delete") == prior_deletes and audit_records == [audit_body]
    ledger.close()


def test_legacy_v1_authorization_never_covers_source_audit(tmp_path):
    candidate, _, ledger, kwargs, _, _ = setup_job(tmp_path)
    path = kwargs["authorization_path"]
    payload = json.loads(path.read_bytes())
    payload["format"] = "greenmind.sql-deletion.v1"
    raw = json.dumps(payload, sort_keys=True).encode()
    path.chmod(0o600)
    path.write_bytes(raw)
    with pytest.raises(ArchiveBlocked, match="authorization"):
        sql.activation(path, hashlib.sha256(raw).hexdigest(), candidate, NOW)
    ledger.close()
