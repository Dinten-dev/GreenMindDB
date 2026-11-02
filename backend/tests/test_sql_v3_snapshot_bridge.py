"""Offline cryptography and importer ordering; no Source or production access."""

import copy
import json
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import sql_audit_v3_backup as audit_backup
from app.tiered_archive import sql_audit_v3_snapshot_bridge as bridge
from app.tiered_archive import sql_audit_v3_source_snapshot as source
from tests.test_sql_audit_v3 import payload


def challenge(private):
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    identity = {"database": "plantdb", "database_oid": 17, "server_address": "127.0.0.1/32",
                "server_port": 5432, "postmaster_start": "synthetic", "role": "source_dba"}
    return {"format": "greenmind.sql-v3-source-snapshot-request.v1", "snapshot": "00000003-0000001B-1",
            "lease_session": "1" * 32, "nonce": "2" * 64, "archive_public": public,
            "binding_sha256": "3" * 64, "dba_identity": identity, "reader_identity": dict(identity),
            "schema_sha256": "4" * 64, "epoch": "5" * 32, "genesis_sha256": "6" * 64,
            "expires_at": "2030-01-01T00:00:00+00:00", "producer_sha256": "7" * 64, "source_manifest_sha256": "8" * 64}


def test_ciphertext_only_and_request_bound_to_both_public_keys():
    private = X25519PrivateKey.generate()
    request = challenge(private)
    original = payload()
    original["data"]["approvals"][0]["body"] = "SYNTHETIC_SECRET_AUDIT_VALUE"
    frame = source.encrypt_payload(original, request)
    encoded = json.dumps(frame)
    assert "SYNTHETIC_SECRET_AUDIT_VALUE" not in encoded
    assert bridge.decrypt_frame(private, request, frame) == original
    for key in ("nonce", "source_public", "iv", "ciphertext", "lease_session", "snapshot_sha256"):
        changed = copy.deepcopy(frame)
        changed[key] = ("0" if changed[key][0] != "0" else "1") + changed[key][1:]
        with pytest.raises(ArchiveBlocked):
            bridge.decrypt_frame(private, request, changed)
    for key in ("snapshot", "schema_sha256", "source_manifest_sha256", "producer_sha256"):
        changed_request = copy.deepcopy(request)
        changed_request[key] = "f" + changed_request[key][1:]
        with pytest.raises(ArchiveBlocked):
            bridge.decrypt_frame(private, changed_request, frame)
    with pytest.raises(ArchiveBlocked):
        bridge.decrypt_frame(X25519PrivateKey.generate(), request, frame)


def test_combined_schema_is_encrypted_and_bound():
    private = X25519PrivateKey.generate()
    request = challenge(private)
    request.update(schema_scope_sha256="9" * 64, pg_client_dir="/root/client", pg_client_manifest_sha256="a" * 64)
    schema = b"PGDMP synthetic private schema"
    frame = source.encrypt_payload(payload(), request, schema=schema)
    assert "synthetic private schema" not in json.dumps(frame)
    assert bridge.decrypt_frame(private, request, frame) == (payload(), schema)
    request["schema_scope_sha256"] = "b" * 64
    with pytest.raises(ArchiveBlocked):
        bridge.decrypt_frame(private, request, frame)


def test_source_schema_dump_is_bounded_and_uses_no_password_argv(monkeypatch):
    from app.tiered_archive import backup

    secret = "synthetic-password-value"
    seen = []
    def client(name):
        seen.append(name)
        return [sys.executable, "-c", "import sys;sys.stdout.buffer.write(b'PGDMP synthetic')"]
    monkeypatch.setattr(backup, "client_command", client)
    assert source.schema_dump("00000001-00000001-1", secret, "/synthetic", "a" * 64,
                              checkpoint=lambda: None) == b"PGDMP synthetic"
    assert seen == ["pg_dump"]
    monkeypatch.setattr(source, "SCHEMA_BYTES", 5)
    with pytest.raises(ArchiveBlocked, match="bound"):
        source.schema_dump("00000001-00000001-1", secret, "/synthetic", "a" * 64, checkpoint=lambda: None)


def test_private_schema_response_is_session_bound_and_cleaned(tmp_path):
    from app.tiered_archive.codec import digest

    b = bridge.RootSnapshotBridge(binding_path=Path("/a"), binding_sha256="a" * 64,
                                  dba_identity_path=Path("/b"), dba_identity_sha256="b" * 64,
                                  challenge_file=Path("/c"), frame_file=Path("/d"),
                                  source_manifest_sha256="c" * 64, producer_sha256="d" * 64)
    b.schema_capture({"synthetic": True}, tmp_path)
    b.schema, b.snapshot = b"PGDMP synthetic schema", "00000001-00000001-1"
    request = {"snapshot": b.snapshot, "scope_sha256": digest(b.schema_scope),
               "program_sha256": b.producer_sha256, "transport_binding_sha256": b.binding_sha256}
    with pytest.raises(ArchiveBlocked):
        b.schema_response({**request, "snapshot": "different"}, checkpoint=lambda: None)
    folder, receipt_sha = b.schema_response(request, checkpoint=lambda: None)
    assert (folder / "schema.dump").read_bytes() == b"PGDMP synthetic schema"
    assert len(receipt_sha) == 64
    with pytest.raises(ArchiveBlocked):
        b.schema_response(request, checkpoint=lambda: None)
    b.cleanup()
    assert not folder.exists()


def test_importer_binds_exported_snapshot_before_any_select(monkeypatch):
    import app.tiered_archive.sql_deletion_guard as guard
    private = X25519PrivateKey.generate()
    request = challenge(private)
    exporter_snapshot = request["snapshot"]
    calls = []
    class Cursor:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return None
        def execute(self, statement, params):
            calls.append((statement, params))
    class Connection:
        def set_session(self, **settings):
            calls.append(("session", settings))
        def cursor(self):
            return Cursor()
    def identity(_db):
        assert calls[1] == ("SET TRANSACTION SNAPSHOT %s", (exporter_snapshot,))
        calls.append(("SELECT synthetic identity", None))
        return request["dba_identity"]
    def collect(_db, snapshot, **_kwargs):
        assert snapshot == exporter_snapshot
        calls.append(("SELECT synthetic audit", None))
        return payload()
    monkeypatch.setattr(guard, "_identity", identity)
    monkeypatch.setattr(audit_backup, "snapshot_payload", collect)
    source.import_snapshot(Connection(), request, checkpoint=lambda: None)
    assert [item[0] for item in calls] == ["session", "SET TRANSACTION SNAPSHOT %s", "SELECT synthetic identity", "SELECT synthetic audit"]
    assert calls[0][1]["readonly"] is True and calls[0][1]["autocommit"] is False


def test_snapshot_bridge_defaults_disabled_and_single_use():
    b = bridge.RootSnapshotBridge(binding_path=Path("/a"), binding_sha256="a" * 64,
                                  dba_identity_path=Path("/b"), dba_identity_sha256="b" * 64,
                                  challenge_file=Path("/c"), frame_file=Path("/d"),
                                  source_manifest_sha256="c" * 64, producer_sha256="d" * 64)
    with pytest.raises(ArchiveBlocked):
        b.capture("00000003-0000001B-1", reader_db=None, reader_identity={}, schema_sha256="e" * 64,
                  epoch="f" * 32, genesis_sha256="a" * 64, checkpoint=lambda: None)
    with pytest.raises(ArchiveBlocked, match="replayed"):
        b.capture("00000003-0000001B-1", reader_db=None, reader_identity={}, schema_sha256="e" * 64,
                  epoch="f" * 32, genesis_sha256="a" * 64, checkpoint=lambda: None)


def test_backup_hook_uses_root_bridge_without_reader_audit_access(tmp_path, monkeypatch):
    from types import SimpleNamespace

    original = payload()
    original["data"]["approvals"][0]["body"] = "SYNTHETIC_ROOT_ONLY_VALUE"
    root_bridge = object.__new__(bridge.RootSnapshotBridge)
    calls = []

    def capture(_self, snapshot, **kwargs):
        calls.append((snapshot, kwargs["reader_db"]))
        return original

    monkeypatch.setattr(bridge.RootSnapshotBridge, "capture", capture)
    hook = object.__new__(audit_backup.AuditSnapshotCapture)
    hook.output, hook.key, hook.identity = tmp_path, b"k" * 32, {}
    hook.schema_sha256, hook.epoch, hook.genesis = "a" * 64, "1" * 32, "2" * 64
    hook.root_bridge, hook.reference = root_bridge, None
    class Reader:
        dialect = SimpleNamespace(name="postgresql")
        def execute(self, *_args, **_kwargs):
            raise AssertionError("Reader must not access audit tables")
    reader = Reader()
    reference = hook.capture(reader, "snapshot-synthetic", checkpoint=lambda: None)
    assert calls == [("snapshot-synthetic", reader)]
    assert reference["file"]["bytes"] > 0
    assert b"SYNTHETIC_ROOT_ONLY_VALUE" not in (tmp_path / "sql-audit-v3.bin").read_bytes()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["sql-audit-v3.bin"]
