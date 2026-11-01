import copy

import pytest

from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import sql_audit_v3 as audit
from app.tiered_archive import sql_audit_v3_backup as snapshots


def payload():
    return {
        "format": snapshots.FORMAT,
        "source_identity": {"role": "reader", "oid": 123},
        "schema_sha256": "a" * 64,
        "snapshot_sha256": "b" * 64,
        "structure": {"format": "greenmind.sql-audit-v3-structure.v1", "tables": {k: {"columns": [], "constraints": [], "triggers": [], "grants": [True, False, False]} for k in snapshots.TABLES}, "functions": [[], [], [], [], []], "ingest_triggers": [[]]},
        "data": {
            "generation": [{"singleton": True, "epoch": "1" * 32, "genesis_sha": "2" * 64, "writer_oid": 42}],
            "approvals": [{"slot": 1, "epoch": "1" * 32, "writer_oid": 42, "candidate_sha": "3" * 64, "tenant_sha": "4" * 64, "pk_sha": "5" * 64, "permit_sha": "6" * 64, "body": "{}", "expected": ["7" * 64, "8" * 64], "start_at": "2026-11-01T00:00:00+00:00", "expires_at": "2026-11-01T00:01:00+00:00"}],
            "events": [{"slot": 1, "txid": 55, "created_at": "2026-11-01T00:00:01+00:00"}],
            "rows": [{"slot": 1, "hash": "7" * 64}, {"slot": 1, "hash": "8" * 64}],
            "unknown": [],
        },
    }


def test_plan_is_inert_and_uses_bounded_database_contract():
    result = audit.ddl_plan("writer_role", "1" * 32, "2" * 64)
    ddl = result["ddl"]
    assert result["status"] == "REVIEW_ONLY" and result["executed"] is False
    assert "slot BETWEEN 1 AND 128" in ddl
    assert "cardinality(expected) BETWEEN 1 AND 32" in ddl
    assert "DEFERRABLE INITIALLY DEFERRED" in ddl
    assert "ON CONFLICT(singleton) DO NOTHING" in ddl
    assert "GRANT SELECT ON public.gm_sql_v3_approval" in ddl
    assert 'GRANT SELECT,INSERT' not in ddl
    with pytest.raises(ArchiveBlocked):
        audit.ddl_plan('writer; DROP TABLE x', "1" * 32, "2" * 64)


def test_snapshot_requires_full_operation_rows():
    data = payload()
    assert snapshots.validate(data) == data
    data["data"]["rows"].pop()
    with pytest.raises(ArchiveBlocked, match="incomplete"):
        snapshots.validate(data)


def test_snapshot_rejects_forged_hash_duplicates_and_orphans():
    for change in ("duplicate", "orphan", "unexpected"):
        data = payload()
        if change == "duplicate":
            data["data"]["rows"].append(copy.deepcopy(data["data"]["rows"][0]))
        elif change == "orphan":
            data["data"]["rows"][0]["slot"] = 2
        else:
            data["data"]["rows"][0]["hash"] = "9" * 64
        with pytest.raises(ArchiveBlocked):
            snapshots.validate(data)


def test_unknown_latch_is_recorded_and_never_treated_as_empty():
    data = payload()
    data["data"]["unknown"] = [{"singleton": True, "txid": 56, "created_at": "2026-11-01T00:00:02+00:00"}]
    assert snapshots.validate(snapshots.decode(snapshots.encode(data)))["data"]["unknown"]
    data["data"]["unknown"].append(copy.deepcopy(data["data"]["unknown"][0]))
    with pytest.raises(ArchiveBlocked):
        snapshots.validate(data)


def test_maximum_inventory_is_bounded_and_129th_slot_rejected():
    data = payload()
    base = data["data"]["approvals"][0]
    data["data"]["approvals"] = []
    data["data"]["events"] = []
    data["data"]["rows"] = []
    for slot in range(1, 129):
        hashes = [f"{slot * 100 + i:064x}" for i in range(32)]
        data["data"]["approvals"].append({**base, "slot": slot, "expected": hashes, "body": "\\" * 1024})
        data["data"]["events"].append({"slot": slot, "txid": slot, "created_at": "2026-11-01T00:00:00+00:00"})
        data["data"]["rows"].extend({"slot": slot, "hash": value} for value in hashes)
    snapshots.validate(data)
    assert len(snapshots.encode(data)) <= 1024 * 1024
    data["data"]["approvals"].append({**base, "slot": 129})
    with pytest.raises(ArchiveBlocked, match="bounds"):
        snapshots.validate(data)


def test_default_provisioner_rejects_unprivileged_execution(monkeypatch):
    monkeypatch.setattr("os.geteuid", lambda: 999)
    with pytest.raises(ArchiveBlocked, match="root"):
        audit.provision(None, permit_path="/missing", permit_sha256="a" * 64, binding_path="/missing", binding_sha256="b" * 64, lease_session="1" * 32, checkpoint=lambda: None)


def test_registry_requires_exact_v3_receipt_and_rejects_tampering():
    from app.tiered_archive import registry_v5

    epoch, candidate, manifest, encrypted, plain = ("1" * 32, "2" * 64, "3" * 64, "4" * 64, "5" * 64)
    receipt = {
        "state": "published",
        "source": {"source": "gateway", "dataset": "database_backup"},
        "manifest": {"sha256": manifest},
        "files": [{"backup_format": "schema-master-provider-sql-audit-v3", "audit_v3_plaintext_sha256": plain, "audit_v3_epoch": epoch}, {}, {}, {}, {}, {"sha256": encrypted, "plaintext_sha256": plain}],
    }

    class Ledger:
        def get(self, key):
            return receipt if key == candidate else None

    row = [epoch, candidate, manifest, encrypted, plain]
    registry_v5.validate("sql_audit_v3_backup", row, "production", Ledger())
    for position in range(len(row)):
        damaged = row.copy()
        damaged[position] = "6" * len(row[position])
        with pytest.raises(ArchiveBlocked):
            registry_v5.validate("sql_audit_v3_backup", damaged, "production", Ledger())


def test_restore_rejects_nonisolated_source_database():
    with pytest.raises(ArchiveBlocked):
        snapshots.verify_native_rows("postgresql://writer@source/greenmind", {}, payload(), checkpoint=lambda: None)


def test_provider_capture_binds_real_encrypted_audit_part(tmp_path, monkeypatch):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from app.tiered_archive import backup, codec, native_backup, parquet, schema_provider

    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    key = bytes(range(32))
    original_stat = type(root).stat

    def fixture_stat(path, *args, **kwargs):
        value = original_stat(path, *args, **kwargs)
        if path == root:
            return SimpleNamespace(st_uid=0, st_mode=value.st_mode)
        return value

    monkeypatch.setattr(type(root), "stat", fixture_stat)
    monkeypatch.setattr(schema_provider.os, "geteuid", lambda: 0)
    monkeypatch.setattr(schema_provider, "validate_scope", lambda _: None)
    monkeypatch.setattr(schema_provider, "validate_proof", lambda *_: None)
    monkeypatch.setattr(native_backup, "source_url", lambda *_, **__: None)
    hook = snapshots.AuditSnapshotCapture(output=root, key=key, source_identity={}, schema_sha256="a" * 64, epoch="1" * 32, genesis_sha256="2" * 64)
    at = datetime(2026, 11, 1, tzinfo=UTC)
    proof = {"tables": {}, "provider": {"scope": {}}, "schema_scope": {}}
    data = payload()
    plain = root / "audit-plain"
    plain.write_bytes(snapshots.encode(data))
    audit_file = root / "sql-audit-v3.bin"
    ref = backup.encrypt(plain, audit_file, key, checkpoint=lambda: None)
    plain.unlink()
    audit_ref = {"format": snapshots.FORMAT, "epoch": "1" * 32, "genesis_sha256": "2" * 64, "schema_sha256": "a" * 64, "snapshot_sha256": "b" * 64, "file": ref}

    def fake_dump(*_, **kwargs):
        assert kwargs["audit_capture"] is hook
        paths = [root / f"source-{i}" for i in range(3)]
        for path in paths:
            path.write_bytes(b"synthetic dump")
        return paths, proof, at, audit_ref

    monkeypatch.setattr(backup, "dump", fake_dump)
    candidate, files = schema_provider.capture("unused", "gateway", root, key, {"source": "gateway", "environment": "staging"}, checkpoint=lambda: None, audit_capture=hook)
    assert len(files) == 6 and files[0][1]["backup_format"] == schema_provider.AUDIT_V3_FORMAT
    assert schema_provider.current_descriptor([ref for _, ref in files], "gateway") is False
    body = list(parquet.read(files[0][0], files[0][1]))[0]["body"]
    assert body["audit_v3"] == audit_ref and body["files"][-1] == ref
    assert files[0][1]["audit_v3_epoch"] == audit_ref["epoch"]
    auth = root / "auth-plain"
    backup.decrypt(files[4][0], auth, key, files[4][1]["plaintext_sha256"], checkpoint=lambda: None)
    assert codec.decode(auth.read_bytes())["audit_v3"] == audit_ref
    assert candidate.source == "gateway"


def test_v3_permit_is_independent_and_pins_all_source_rows(tmp_path):
    import hashlib
    import json
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from app.tiered_archive import policy, sql_deletion, sql_deletion_v3

    now = datetime(2026, 11, 1, 12, tzinfo=UTC)
    candidate = SimpleNamespace(environment="production", identity="a" * 64, rows=1)
    pin = {"sql_policy": sql_deletion.SQL_POLICY_SHA256, "candidate": candidate.identity, "runtime_sha256": "b" * 64, "bindings_sha256": "c" * 64, "proof_contract_sha256": "d" * 64, "fence_contract_sha256": "e" * 64}
    data = {"format": "greenmind.sql-deletion.v3", "policy": policy.POLICY_SHA256, **pin, "mode": "pilot", "pilot_id": "1" * 32, "environment": "production", "authorized_at": now.isoformat(), "expires_at": (now + timedelta(minutes=1)).isoformat(), "acceptance": {**pin, "deployed_at": (now - timedelta(days=2)).isoformat(), "observation": {"started_at": (now - timedelta(days=2)).isoformat(), "finished_at": (now - timedelta(days=1)).isoformat(), "maximum_gap_seconds": 600, "samples": 280, "errors": 0, "progress": {"gateway": True, "direct": True}}}, "audit": {"epoch": "2" * 32, "genesis_sha256": "3" * 64, "schema_sha256": "4" * 64, "slot": 1, "pk_sha256": "5" * 64, "source_row_sha256": ["6" * 64]}}
    path = tmp_path / "permit"

    def check(value):
        raw = json.dumps(value).encode()
        path.write_bytes(raw)
        path.chmod(0o600)
        return sql_deletion_v3.activation(path, hashlib.sha256(raw).hexdigest(), candidate, now)

    assert check(data) == data
    data["format"] = "greenmind.sql-deletion.v2"
    with pytest.raises(ArchiveBlocked):
        check(data)
    data["format"] = "greenmind.sql-deletion.v3"
    data["audit"]["source_row_sha256"] = []
    with pytest.raises(ArchiveBlocked):
        check(data)
    with pytest.raises(ArchiveBlocked, match="restore provenance"):
        sql_deletion_v3.require_native_restore_evidence()


def test_v3_history_reconciles_committed_source_and_rejects_unknown():
    import sqlite3
    from types import SimpleNamespace

    from app.tiered_archive import sql_deletion_v3

    epoch, pilot, candidate, auth = "1" * 32, "2" * 32, "3" * 64, "4" * 64
    hashes = ["5" * 64, "6" * 64]
    permit = {"pilot_id": pilot, "candidate": candidate, "runtime_sha256": "7" * 64, "bindings_sha256": "8" * 64, "proof_contract_sha256": "9" * 64, "fence_contract_sha256": "a" * 64, "audit": {"slot": 1, "epoch": epoch, "source_row_sha256": hashes}}
    body = sql_deletion_v3.approval_body(permit, auth)
    receipt = {"state": "deleting", "deletion_intent": {"pilot_id": pilot, "authorization_sha256": auth, "audit_v3_slot": 1, "audit_v3_epoch": epoch, "audit_v3_body": body, "source_row_sha256": hashes}}
    ledger = SimpleNamespace(db=sqlite3.connect(":memory:"), get=lambda _: receipt)
    ledger.db.execute("CREATE TABLE sql_deletion_pilot(pilot_id text,candidate text,authorization_sha256 text)")
    ledger.db.execute("INSERT INTO sql_deletion_pilot VALUES(?,?,?)", (pilot, candidate, auth))

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one(self):
            return self.value

        def all(self):
            return self.value

    class SourceDB:
        unknown = 0
        rows = [(1, value) for value in hashes]

        def execute(self, statement, *_):
            sql = str(statement)
            if "pg_try_advisory" in sql:
                return Result(True)
            if "count(*) FROM public.gm_sql_v3_unknown" in sql:
                return Result(self.unknown)
            if "FROM public.gm_sql_v3_approval" in sql:
                return Result([(1, epoch, candidate, auth, body, hashes)])
            if "FROM public.gm_sql_v3_event" in sql:
                return Result([(1, "32")])
            if "FROM public.gm_sql_v3_row" in sql:
                return Result(self.rows)
            raise AssertionError(sql)

    db = SourceDB()
    assert sql_deletion_v3.verify_history(db, ledger, epoch)["commits"] == 1
    db.rows = db.rows[:1]
    with pytest.raises(ArchiveBlocked, match="audit differs"):
        sql_deletion_v3.verify_history(db, ledger, epoch)
    db.rows = [(1, value) for value in hashes]
    db.unknown = 1
    with pytest.raises(ArchiveBlocked, match="Unknown"):
        sql_deletion_v3.verify_history(db, ledger, epoch)
    ledger.db.close()


def test_v3_writer_guard_and_runtime_default_to_hold(monkeypatch):
    from app.tiered_archive import sql_deletion_guard, sql_runtime

    guard = object.__new__(sql_deletion_guard.ProductionGuardV3)
    with pytest.raises(ArchiveBlocked, match="restore provenance"):
        guard.prepare(None, None, None, None)
    monkeypatch.setattr(sql_runtime.os, "geteuid", lambda: 0)
    monkeypatch.delenv("TIERED_ARCHIVE_SQL_V3_CLI_ENABLED", raising=False)
    with pytest.raises(ArchiveBlocked, match="enablement"):
        sql_runtime.evict("a" * 64, restore_profile_file=None, restore_profile_sha256=None, proof_file=None, proof_sha256=None, authorization_file=None, authorization_sha256=None, snapshot_token_file=None, snapshot_token_sha256=None, operation="evict-v3")


def test_source_approval_rejects_arbitrary_or_cross_pilot_body():
    from app.tiered_archive import sql_deletion_v3

    permit = {"pilot_id": "1" * 32, "candidate": "2" * 64, "runtime_sha256": "3" * 64, "bindings_sha256": "4" * 64, "proof_contract_sha256": "5" * 64, "fence_contract_sha256": "6" * 64}
    auth = "7" * 64
    body = sql_deletion_v3.approval_body(permit, auth)
    assert audit.validate_operation_body(body, permit["candidate"], auth)["pilot_id"] == permit["pilot_id"]
    for text, candidate, permit_hash in ((body, "8" * 64, auth), (body, permit["candidate"], "9" * 64), ("{}", permit["candidate"], auth), (body + " ", permit["candidate"], auth)):
        with pytest.raises(ArchiveBlocked):
            audit.validate_operation_body(text, candidate, permit_hash)


def test_snapshot_rejects_missing_schema_grants_and_oversized_structure():
    data = payload()
    del data["structure"]
    with pytest.raises(ArchiveBlocked):
        snapshots.validate(data)
    data = payload()
    data["structure"]["tables"]["approvals"]["grants"] = [True, True, False]
    with pytest.raises(ArchiveBlocked, match="structure"):
        snapshots.validate(data)
    data = payload()
    data["structure"]["functions"][0] = ["x" * 65537]
    with pytest.raises(ArchiveBlocked, match="schema proof exceeds"):
        snapshots.validate(data)


def test_approval_ttl_never_outlives_source_lease():
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 11, 1, tzinfo=UTC)
    audit.check_lease_expiry(now + timedelta(seconds=12), now, 12.0)
    for delta, remaining in ((13, 12), (0, 12), (1, 0)):
        with pytest.raises(ArchiveBlocked, match="lease"):
            audit.check_lease_expiry(now + timedelta(seconds=delta), now, remaining)


def test_source_dba_actor_defaults_inert_and_masks_unknown_outcome(monkeypatch):
    from app.tiered_archive import sql_audit_v3_source as source

    calls = []
    monkeypatch.setattr(source.os, "geteuid", lambda: 0)
    monkeypatch.delenv("TIERED_ARCHIVE_SQL_V3_SOURCE_DBA_ENABLED", raising=False)
    monkeypatch.setattr(source, "_observe_container", lambda: calls.append("inspect"))
    inputs = dict(permit_path="/no", permit_sha256="a" * 64, binding_path="/no", binding_sha256="b" * 64, lease_session="1" * 32, checkpoint=lambda: None)
    with pytest.raises(ArchiveBlocked, match="enablement"):
        source.provision_source(**inputs)
    assert calls == []
    monkeypatch.setenv("TIERED_ARCHIVE_SQL_V3_SOURCE_DBA_ENABLED", "true")
    observed = iter([({"pid": 11, "started_at": "old"}, "private-password"), ({"pid": 12, "started_at": "new"}, "private-password")])
    monkeypatch.setattr(source, "_observe_container", lambda: next(observed))

    class Engine:
        def dispose(self):
            calls.append("disposed")

    monkeypatch.setattr(source, "create_engine", lambda *_a, **_k: Engine())
    monkeypatch.setattr(source.sql_audit_v3, "provision", lambda *_a, **_k: {"slot": 1})
    with pytest.raises(ArchiveBlocked, match="reconciliation") as error:
        source.provision_source(**inputs, enabled=True)
    assert "private-password" not in str(error.value)
    assert calls == ["disposed"]


def test_source_dba_adapter_uses_exact_container_and_never_reports_secret(monkeypatch):
    import json
    import stat
    from types import SimpleNamespace

    from app.tiered_archive import sql_audit_v3_source as source

    metadata = {"Id": source.CONTAINER, "Image": source.IMAGE, "State": {"Running": True, "Pid": 123, "StartedAt": "2026-11-01T00:00:00Z"}, "Config": {"Env": ["POSTGRES_USER=admin", "POSTGRES_PASSWORD=private-test-secret"]}}
    original_lstat = type(source.SOCKET).lstat

    def fixture_lstat(path, *args, **kwargs):
        if path == source.SOCKET:
            return SimpleNamespace(st_mode=stat.S_IFSOCK | 0o660, st_uid=0)
        return original_lstat(path, *args, **kwargs)

    class Docker:
        def __init__(self, *_a, **_k):
            pass

        def request(self, method, path):
            assert method == "GET" and path == "/containers/" + source.CONTAINER + "/json"

        def getresponse(self):
            return SimpleNamespace(status=200, read=lambda _: json.dumps(metadata).encode())

        def close(self):
            pass

    monkeypatch.setattr(type(source.SOCKET), "lstat", fixture_lstat)
    monkeypatch.setattr(source, "_Docker", Docker)
    generation, password = source._observe_container()
    assert generation == {"pid": 123, "started_at": "2026-11-01T00:00:00Z"} and password == "private-test-secret"
    metadata["Image"] = "sha256:" + "0" * 64
    with pytest.raises(ArchiveBlocked) as error:
        source._observe_container()
    assert "private-test-secret" not in str(error.value)
    metadata["Image"] = source.IMAGE
    metadata["Config"]["Env"].append("POSTGRES_PASSWORD=another-secret")
    with pytest.raises(ArchiveBlocked, match="ambiguous") as error:
        source._observe_container()
    assert "secret" not in str(error.value)


def test_source_dba_adapter_disposes_after_success_with_mocked_dependencies(monkeypatch):
    from app.tiered_archive import sql_audit_v3_source as source

    monkeypatch.setattr(source.os, "geteuid", lambda: 0)
    monkeypatch.setenv("TIERED_ARCHIVE_SQL_V3_SOURCE_DBA_ENABLED", "true")
    monkeypatch.setattr(source, "_observe_container", lambda: ({"pid": 123, "started_at": "same"}, "private-test-secret"))
    disposed = []

    class Engine:
        def dispose(self):
            disposed.append(True)

    def factory(url, **_kwargs):
        assert url.host == "127.0.0.1" and url.database == "plantdb" and url.username == "admin"
        return Engine()

    monkeypatch.setattr(source, "create_engine", factory)
    monkeypatch.setattr(source.sql_audit_v3, "provision", lambda *_a, **_k: {"slot": 1})
    result = source.provision_source(permit_path="/no", permit_sha256="a" * 64, binding_path="/no", binding_sha256="b" * 64, lease_session="1" * 32, checkpoint=lambda: None, enabled=True)
    assert result == {"slot": 1} and disposed == [True]
    assert "private-test-secret" not in str(result)
