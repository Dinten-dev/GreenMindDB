"""Same exported snapshot and completion joins, without native or network calls."""
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import (
    backup,
    native_backup,
    parent_snapshot,
    parent_snapshot_source,
    schema_provider,
)


def test_encrypted_parent_frame_binds_entire_request():
    private = X25519PrivateKey.generate()
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    request = {"archive_public": public, "nonce": "a" * 64, "lease_session": "b" * 32,
               "snapshot": "00000001-00000002-1", "snapshot_sha256": parent_snapshot.snapshot_sha("00000001-00000002-1"),
               "source": "gateway", "provider_scope_sha256": "c" * 64}
    response = {"format": "example", "no_raw_rows": True}
    frame = parent_snapshot_source.encrypt_response(response, request)
    assert parent_snapshot.decrypt_frame(private, request, frame) == response
    for changed in ({**request, "source": "direct"}, {**request, "provider_scope_sha256": "d" * 64}):
        with pytest.raises(ArchiveBlocked):
            parent_snapshot.decrypt_frame(private, changed, frame)
    with pytest.raises(ArchiveBlocked):
        parent_snapshot.decrypt_frame(private, request, {**frame, "nonce": "e" * 64})


@pytest.mark.parametrize("counts", [(3, 1, 0), (3, 0, 1), (-1, 0, 0)])
def test_direct_closure_never_synthesizes_missing_parents(counts):
    db = SimpleNamespace(execute=lambda *_: SimpleNamespace(one=lambda: counts))
    with pytest.raises(ArchiveBlocked):
        parent_snapshot.direct_closure(db, checkpoint=lambda: None)


def test_direct_closure_uses_actual_session_and_device_joins():
    calls = []
    def execute(query):
        calls.append(str(query))
        return SimpleNamespace(one=lambda: (18917, 0, 0))
    assert parent_snapshot.direct_closure(SimpleNamespace(execute=execute), checkpoint=lambda: None)["segments"] == 18917
    assert "public.direct_device" in calls[0] and "public.direct_session" in calls[0]
    assert "s.device_id=x.device_id" in calls[0] and "s.id=x.session_id" in calls[0]


def test_dump_calls_typed_hook_at_export_before_any_dump(tmp_path, monkeypatch):
    source = "direct"
    snapshot = "00000001-00000002-1"
    scope = {"source": source, "environment": "production", "config_relations": []}
    capture = object.__new__(parent_snapshot.ParentSnapshotCapture)
    capture.source, capture.output = source, tmp_path
    events = []
    monkeypatch.setattr(parent_snapshot.ParentSnapshotCapture, "require_scope", lambda _self, _scope: None)
    monkeypatch.setattr(parent_snapshot.ParentSnapshotCapture, "capture", lambda _self, db, snap, **_kw: events.append(("hook", db, snap)))
    monkeypatch.setattr(backup.os, "geteuid", lambda: 0)
    monkeypatch.setattr(schema_provider, "validate_scope", lambda _scope: None)
    monkeypatch.setattr(schema_provider, "validate_proof", lambda *_: None)
    monkeypatch.setattr(schema_provider, "capture_provider", lambda *_a, **_k: None)
    monkeypatch.setattr(schema_provider, "inspect_dump", lambda *_a, **_k: None)
    monkeypatch.setattr(native_backup, "source_url", lambda *_a, **_k: None)
    monkeypatch.setattr(native_backup, "structure_proof", lambda *_a, **_k: {})
    monkeypatch.setattr(native_backup, "jobs_proof", lambda *_a: "f" * 64)
    monkeypatch.setattr(native_backup, "sequence_proof", lambda *_a, **_k: {})
    monkeypatch.setattr(backup, "master_tables", lambda *_a, **_k: ("direct_device", "direct_session", "direct_enrollment"))
    monkeypatch.setattr(backup, "check_master_tables", lambda *_a, **_k: None)
    monkeypatch.setattr(backup, "master_proof", lambda *_a, **_k: {"version": 3, "tables": {}})
    monkeypatch.setattr(backup, "client_command", lambda _name: ["/pinned/pg_dump"])
    class DB:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def execution_options(self, **_): return self
        def begin(self): return self
        def execute(self, query):
            sql = str(query)
            if sql == "SELECT pg_export_snapshot()":
                events.append(("export", self))
                return SimpleNamespace(scalar_one=lambda: snapshot)
            if sql == "SELECT transaction_timestamp()":
                return SimpleNamespace(scalar_one=lambda: datetime.now(UTC))
            assert sql == "SET TRANSACTION READ ONLY"
    class Process:
        returncode = 0
        def __init__(self, command, **_):
            events.append(("dump", command[command.index("--snapshot") + 1]))
            Path(command[command.index("--file") + 1]).write_bytes(b"actual-test-dump")
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def poll(self): return 0
    monkeypatch.setattr("sqlalchemy.create_engine", lambda *_a, **_k: SimpleNamespace(connect=DB, dispose=lambda: None))
    monkeypatch.setattr(backup.subprocess, "Popen", Process)
    files, _, _ = backup.dump("postgresql://reader:fixture@127.0.0.1:5432/direct", source, tmp_path,
                             checkpoint=lambda: None, with_proof=True, provider_scope=scope, parent_capture=capture)
    assert [e[0] for e in events] == ["export", "hook", "dump", "dump", "dump"]
    assert events[0][1] is events[1][1] and events[1][2] == snapshot
    assert all(event[1] == snapshot for event in events[2:])
    assert [p.name for p in files] == ["schema.dump", "masters.dump", "provider.dump"]


def test_untyped_parent_hook_is_rejected(tmp_path):
    with pytest.raises(ArchiveBlocked, match="Exact Root same-snapshot parent hook"):
        backup.dump("postgresql://fixture@127.0.0.1/test", "gateway", tmp_path,
                    checkpoint=lambda: None, parent_capture=object())


def test_two_source_relays_do_not_silently_share_one_frame(tmp_path):
    with pytest.raises(ArchiveBlocked, match="Combined parent and SQL-audit"):
        backup.dump("postgresql://fixture@127.0.0.1/test", "gateway", tmp_path,
                    checkpoint=lambda: None, parent_capture=object(), audit_capture=object())


def test_parent_imports_exported_snapshot_before_first_select(tmp_path, monkeypatch):
    from app.raw_archive import parent_capture
    calls = []
    class DB:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def execution_options(self, **_): return self
        def begin(self): return self
        @property
        def connection(self):
            return SimpleNamespace(driver_connection=SimpleNamespace(cursor=lambda: SimpleNamespace(close=lambda: None)))
        def execute(self, statement, *_):
            sql = str(statement)
            calls.append(sql)
            if sql == "SELECT pg_current_snapshot()::text":
                return SimpleNamespace(scalar_one=lambda: "1:2:")
            return None
    db = DB()
    engine = SimpleNamespace(connect=lambda: db)
    monkeypatch.setattr(parent_capture, "scope", lambda _source: {"tables": []})
    monkeypatch.setattr(parent_capture.scientific_capture, "_engine_binding", lambda *_: None)
    monkeypatch.setattr(parent_capture.scientific_capture, "_source_resources", lambda *_: None)
    monkeypatch.setattr(parent_capture, "_identity", lambda *_: {"test": "reader"})
    result = parent_capture.capture(engine, "gateway", tmp_path / "capture", checkpoint=lambda: None,
        lease_deadline=float("inf"), schema_master_sha256="a" * 64, provider_scope_sha256="b" * 64,
        exported_snapshot="00000001-00000002-1")
    assert calls[:2] == ["SET TRANSACTION READ ONLY", "SET TRANSACTION SNAPSHOT :snapshot"]
    assert calls.index("SET TRANSACTION SNAPSHOT :snapshot") < next(i for i, item in enumerate(calls) if item.startswith("SELECT"))
    assert result["capture"]["backup_snapshot_consistency_proven"] is False
    assert result["capture"]["same_snapshot_as_previous_science"] is False


def test_completion_binds_actual_dump_and_published_files(tmp_path, monkeypatch):
    import os
    from datetime import timedelta

    from app.tiered_archive import parquet, restore_inputs
    from app.tiered_archive.codec import digest
    from app.tiered_archive.ledger import Ledger
    from app.tiered_archive.policy import Candidate

    monkeypatch.setattr(schema_provider, "validate_proof", lambda *_: None)
    class Owned:
        def __init__(self, actual):
            self.actual = actual
        def __getattr__(self, name):
            return 0 if name == "st_uid" else getattr(self.actual, name)
    class RootOS:
        def __getattr__(self, name):
            return getattr(os, name)
        def fstat(self, fd):
            return Owned(os.fstat(fd))
    monkeypatch.setattr(parent_snapshot, "os", RootOS())
    original_lstat = Path.lstat
    def owned(path, *args, **kwargs):
        actual = original_lstat(path, *args, **kwargs)
        if path.parent == tmp_path:
            return Owned(actual)
        return actual
    monkeypatch.setattr(Path, "lstat", owned)
    hook = object.__new__(parent_snapshot.ParentSnapshotCapture)
    hook.source, hook.output, hook.receipt_dir, hook.used = "direct", tmp_path, tmp_path, True
    scope = {"source": "direct", "environment": "production"}
    hook.provider_scope_sha256 = digest(scope)
    private = X25519PrivateKey.generate()
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    request = {"archive_public": public, "nonce": "a" * 64, "lease_session": "b" * 32,
               "snapshot": "00000001-00000002-1", "snapshot_sha256": parent_snapshot.snapshot_sha("00000001-00000002-1")}
    response = {"request_sha256": hashlib.sha256(parent_snapshot.canonical(request)).hexdigest(),
                "capture_sha256": "e" * 64, "capture": {"capture_id": "a" * 32}}
    frame = parent_snapshot_source.encrypt_response(response, request)
    raw_frame = parent_snapshot.canonical(frame)
    frame_file = tmp_path / "source-frame.json"
    frame_file.write_bytes(raw_frame)
    frame_file.chmod(0o600)
    monkeypatch.setattr(restore_inputs, "frozen", lambda _path: (1, 2, 3))
    hook._private, hook._request, hook.frame_file = private, request, frame_file
    hook._frame_sha256 = hashlib.sha256(raw_frame).hexdigest()
    hook._response_sha256 = hashlib.sha256(parent_snapshot.canonical(response)).hexdigest()
    hook.result = {"capture_id": "a" * 32, "snapshot_sha256": "b" * 64,
                   "capture_sha256": "e" * 64, "request_sha256": response["request_sha256"]}
    proof = {"provider": {"scope": scope}, "tables": {}}
    refs = []
    for n, label in enumerate(("schema", "masters", "provider"), 1):
        plain = tmp_path / (label + ".dump")
        plain.write_bytes((label + "-real").encode())
        plain.chmod(0o600)
        sealed = tmp_path / f"{n:04d}.bin"
        refs.append((sealed, backup.encrypt(plain, sealed, b"k" * 32, checkpoint=lambda: None)))
    auth = tmp_path / "0004.bin"
    auth.write_bytes(b"authenticated backup file")
    auth.chmod(0o600)
    refs.append((auth, {"format": "bin", "sha256": hashlib.sha256(auth.read_bytes()).hexdigest(),
                        "bytes": auth.stat().st_size, "plaintext_sha256": "c" * 64}))
    at = datetime(2026, 11, 2, tzinfo=UTC)
    index = tmp_path / "0000.parquet"
    row = {"identity": "i", "organization": "00000000-0000-0000-0000-000000000000", "sensor": "system", "time_us": 1, "body": {"ok": True}}
    index_ref = parquet.write(index, [row])
    index_ref.update(format="parquet", master_proof_sha256=digest(proof), provider_scope_sha256=digest(scope))
    candidate = Candidate("production", "direct", "database_backup", row["organization"], at-timedelta(seconds=1), at,
                          index_ref["content_sha256"], 1, "features", at)
    files = [(index, index_ref), *refs]
    join = hook.complete(candidate, files, proof, checkpoint=lambda: None)
    assert join["value"]["parent_and_backup_same_exported_snapshot"] is True
    assert join["value"]["independent_backup_readback"] is False
    assert len(join["value"]["backup_file_sha256"]) == 5
    ledger = Ledger(tmp_path / "ledger")
    receipt = ledger.save(candidate, "published", {"files": [item for _, item in files],
                                                     "manifest": {"sha256": "d" * 64}})
    with pytest.raises(ArchiveBlocked):
        hook.record_publication({**receipt, "files": receipt["files"][:-1]}, ledger=ledger, checkpoint=lambda: None)
    join["value"]["archive_accepted"] = True
    with pytest.raises(ArchiveBlocked, match="completion join changed"):
        hook.record_publication(receipt, ledger=ledger, checkpoint=lambda: None)
    join["value"]["archive_accepted"] = False
    published = hook.record_publication(receipt, ledger=ledger, checkpoint=lambda: None)
    assert Path(published["path"]).is_file()
