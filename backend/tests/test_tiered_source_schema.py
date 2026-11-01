"""Local private-file/interface tests; no admitted native Root transport exists."""

import copy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import backup, database_restore, schema_provider, source_schema
from app.tiered_archive.codec import digest

SNAPSHOT = "00000003-0000001B-1"
TOC = (
    b";     dbname: plantdb\n;     Format: CUSTOM\n"
    b";     Dumped from database version: 15.10\n"
    b";     Dumped by pg_dump version: 15.10\n"
    b"1; 3079 100 EXTENSION - timescaledb -\n"
)


class DB:
    isolation = "repeatable read"
    read_only = "on"
    active = True

    def execute(self, statement):
        assert "transaction_isolation" in str(statement)
        return SimpleNamespace(one=lambda: (self.isolation, self.read_only))

    def in_transaction(self):
        return self.active


@pytest.fixture
def capture(tmp_path, monkeypatch):
    """Synthetic production contract replaces only the separate v2 dependency."""
    monkeypatch.setattr(source_schema, "ROOT_UID", os.geteuid())
    scope = {
        "environment": "production",
        "source": "gateway",
        "database": "plantdb",
        "source_identity_sha256": "a" * 64,
        "config_relations": [],
        "schema_toc_sha256": digest(schema_provider.toc(TOC, database="plantdb")),
    }

    def separate_v2_contract(value):
        assert value == scope
        return value

    monkeypatch.setattr(schema_provider, "validate_scope", separate_v2_contract)
    monkeypatch.setattr(
        schema_provider, "capture_provider", lambda db, value, **kw: separate_v2_contract(value)
    )
    folder = (tmp_path / "private-response").resolve()
    folder.mkdir(mode=0o700)
    target_folder = (tmp_path / "private-target").resolve()
    target_folder.mkdir(mode=0o700)
    payload = b"PGDMP" + b"x" * (1024**2 + 37)

    def response(request, *, checkpoint):
        checkpoint()
        response.request = copy.deepcopy(request)
        (folder / "schema.dump").write_bytes(payload)
        (folder / "schema.dump").chmod(0o600)
        receipt = {
            "format": "greenmind.private-source-schema-response.v1",
            "request_sha256": digest(request),
            "owned_wait_exit": 0,
            "source_unchanged": True,
            "schema": {
                "name": "schema.dump",
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            },
        }
        if response.alter:
            response.alter(receipt, folder)
        raw = json.dumps(receipt, sort_keys=True).encode()
        (folder / "response.json").write_bytes(raw)
        (folder / "response.json").chmod(0o600)
        return folder, hashlib.sha256(raw).hexdigest()

    response.alter = None

    def client(_command, _env, *, checkpoint, output):
        checkpoint()
        output.write_bytes(TOC)

    monkeypatch.setattr(database_restore, "run_client", client)
    monkeypatch.setattr(backup, "client_command", lambda name: ["/synthetic-pinned/" + name])
    adapter = source_schema.SourceSchemaCapture(
        scope,
        private_response=response,
        source_program_sha256="b" * 64,
        transport_binding_sha256="c" * 64,
    )
    return SimpleNamespace(
        adapter=adapter,
        response=response,
        folder=folder,
        target=target_folder / "schema.dump",
        payload=payload,
        scope=scope,
    )


def run(capture, db=None, snapshot=SNAPSHOT, source="gateway"):
    ticks = []
    result = capture.adapter.capture(
        db=db or DB(),
        source=source,
        snapshot=snapshot,
        target=capture.target,
        checkpoint=lambda: ticks.append(True),
    )
    return result, ticks


def test_private_stream_is_complete_and_exact_schema_command(capture):
    result, ticks = run(capture)
    assert capture.target.read_bytes() == capture.payload
    assert capture.target.stat().st_mode & 0o777 == 0o600
    assert result["schema_sha256"] == hashlib.sha256(capture.payload).hexdigest()
    assert len(ticks) >= 5
    request = capture.response.request
    assert request["snapshot"] == SNAPSHOT and request["database_oid"] == 16384
    assert (
        request["source_container"]
        == "fa5679cd84c16b5024251bb1f93414201fb091900af590cb63a7ecc087dddb71"
    )
    assert request["pg_dump_args"] == [
        "--no-password",
        "--format=custom",
        "--schema-only",
        "--no-owner",
        "--no-acl",
        "--no-blobs",
        "--snapshot=" + SNAPSHOT,
        "--dbname=plantdb",
    ]
    assert request["application_data_authorized"] is False
    assert not any("key" in k or "password" in k for k in request)


def test_missing_existing_transport_blocks_before_any_database_or_response_call(capture):
    capture.adapter.private_response = None
    with pytest.raises(ArchiveBlocked, match="transport is not bound"):
        run(capture, db=object())
    assert not capture.target.exists()


@pytest.mark.parametrize(
    "field,value", [("isolation", "read committed"), ("read_only", "off"), ("active", False)]
)
def test_no_detached_or_readwrite_snapshot(capture, field, value):
    db = DB()
    setattr(db, field, value)
    with pytest.raises(ArchiveBlocked, match="live read-only RR"):
        run(capture, db=db)
    assert not capture.target.exists()


@pytest.mark.parametrize("snapshot,source", [("x;SELECT 1", "gateway"), (SNAPSHOT, "direct")])
def test_exact_gateway_snapshot_only(capture, snapshot, source):
    with pytest.raises(ArchiveBlocked, match="exported PostgreSQL snapshot"):
        run(capture, snapshot=snapshot, source=source)


@pytest.mark.parametrize(
    "change",
    [
        lambda r, p: r.update(request_sha256="e" * 64),
        lambda r, p: r.update(owned_wait_exit=1),
        lambda r, p: r.update(owned_wait_exit=False),
        lambda r, p: r.update(source_unchanged=False),
        lambda r, p: r["schema"].update(name="../outside"),
        lambda r, p: r["schema"].update(bytes=source_schema.MAX_BYTES + 1),
        lambda r, p: r["schema"].update(sha256="e" * 64),
        lambda r, p: (p / "schema.dump").chmod(0o644),
        lambda r, p: os.link(p / "schema.dump", p / "unexpected-second-link"),
    ],
)
def test_private_response_identity_or_terminal_changes_are_rejected(capture, change):
    capture.response.alter = change
    with pytest.raises(ArchiveBlocked):
        run(capture)


def test_existing_destination_never_overwritten(capture):
    capture.target.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        run(capture)
    assert capture.target.read_bytes() == b"existing"


def test_even_matching_toc_pin_never_authorizes_application_data(capture, monkeypatch):
    toc = TOC + b"2; 0 42 TABLE DATA public users owner\n"
    capture.scope["schema_toc_sha256"] = digest(schema_provider.toc(toc, database="plantdb"))
    monkeypatch.setattr(
        database_restore, "run_client", lambda _c, _e, **kw: kw["output"].write_bytes(toc)
    )
    with pytest.raises(ArchiveBlocked, match="Schema-only archive unexpectedly contains DATA"):
        run(capture)


def test_without_new_provider_contract_real_product_still_rejects_production():
    # This branch intentionally has no provider-v2 patch yet. Missing stream
    # binding alone never upgrades an environment or relabels Staging data.
    with pytest.raises(ArchiveBlocked):
        schema_provider.validate_scope({"environment": "production"})


def test_backup_hook_keeps_three_parts_in_one_exported_snapshot(tmp_path, monkeypatch):
    """Producer integration only; v2/transport/data proof are separate contracts."""
    from datetime import UTC, datetime

    from app.tiered_archive import backup_scope, native_backup

    scope = {
        "environment": "production",
        "source": "gateway",
        "database": "plantdb",
        "config_relations": [{"schema": "_timescaledb_catalog", "name": "hypertable"}],
    }
    captured = datetime(2026, 11, 1, tzinfo=UTC)
    auth = {"synthetic_exact23_both_auth_absent": True}
    tables = (*backup.LEGACY_MASTER_TABLES_V2, "form_submission")
    commands = []
    hooks = []
    provider_checks = []
    toc_checks = []

    class DB:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execution_options(self, **kwargs):
            assert kwargs == {"isolation_level": "REPEATABLE READ"}
            return self

        def begin(self):
            return self

        def execute(self, query):
            query = str(query)
            if query == "SELECT pg_export_snapshot()":
                value = SNAPSHOT
            elif query == "SELECT transaction_timestamp()":
                value = captured
            else:
                assert query == "SET TRANSACTION READ ONLY"
                value = None
            return SimpleNamespace(scalar_one=lambda: value)

    monkeypatch.setattr(
        "sqlalchemy.create_engine",
        lambda *_a, **_kw: SimpleNamespace(connect=DB, dispose=lambda: None),
    )
    monkeypatch.setattr(backup.os, "geteuid", lambda: 0)
    monkeypatch.setattr(native_backup, "source_url", lambda *_a, **_kw: None)
    monkeypatch.setattr(schema_provider, "validate_scope", lambda value: value)
    monkeypatch.setattr(backup, "fresh_schema_scope", lambda *_a, **_kw: auth)
    monkeypatch.setattr(backup_scope, "capture_scope", lambda *_a, **_kw: auth)
    monkeypatch.setattr(backup, "master_tables", lambda *_a, **_kw: tables)
    monkeypatch.setattr(backup, "check_master_tables", lambda *_a, **_kw: tables)
    monkeypatch.setattr(
        backup, "master_proof", lambda *_a, **_kw: {"version": 4, "tables": {n: {} for n in tables}}
    )
    monkeypatch.setattr(native_backup, "structure_proof", lambda *_a, **_kw: {})
    monkeypatch.setattr(native_backup, "jobs_proof", lambda *_a, **_kw: "a" * 64)
    monkeypatch.setattr(native_backup, "sequence_proof", lambda *_a, **_kw: {})
    monkeypatch.setattr(
        schema_provider, "capture_provider", lambda _db, value, **_kw: provider_checks.append(value)
    )
    monkeypatch.setattr(schema_provider, "validate_proof", lambda *_a: None)
    monkeypatch.setattr(
        schema_provider,
        "inspect_dump",
        lambda path, value, role, *_a, **_kw: toc_checks.append((path.name, role, value)),
    )
    adapter = source_schema.SourceSchemaCapture(scope)
    monkeypatch.setattr(
        source_schema.SourceSchemaCapture,
        "require_scope",
        lambda self, value: hooks.append(("scope", value)),
    )

    def schema(self, **kwargs):
        assert kwargs["source"] == "gateway" and kwargs["snapshot"] == SNAPSHOT
        hooks.append(("schema", kwargs["snapshot"]))
        kwargs["checkpoint"]()
        kwargs["target"].write_bytes(b"PGDMPsynthetic source schema")

    monkeypatch.setattr(source_schema.SourceSchemaCapture, "capture", schema)
    monkeypatch.setattr(backup, "client_command", lambda name: ["/synthetic/" + name])

    class Process:
        returncode = 0

        def __init__(self, command, **kwargs):
            assert "PGPASSWORD" in kwargs["env"]  # Existing Reader only; no Root admin secret.
            assert "--schema-only" not in command
            commands.append(command)
            Path(command[command.index("--file") + 1]).write_bytes(b"synthetic data")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def poll(self):
            return 0

    monkeypatch.setattr(backup.subprocess, "Popen", Process)
    target = tmp_path / "capture"
    target.mkdir(mode=0o700)
    files, proof, at = backup.dump(
        "postgresql://reader:synthetic@127.0.0.1:5432/plantdb",
        "gateway",
        target,
        checkpoint=lambda: None,
        with_proof=True,
        provider_scope=scope,
        schema_capture=adapter,
    )
    assert [p.name for p in files] == ["schema.dump", "masters.dump", "provider.dump"]
    assert len(commands) == 2 and all(c[c.index("--snapshot") + 1] == SNAPSHOT for c in commands)
    assert all("--data-only" in c for c in commands)
    assert hooks == [("scope", scope), ("schema", SNAPSHOT)]
    assert provider_checks == [scope, scope]
    assert [role for _, role, _ in toc_checks] == ["schema", "masters", "provider"]
    assert proof["version"] == 5 and at == captured


def test_hook_never_weakens_legacy_or_untyped_backup(tmp_path):
    with pytest.raises(ArchiveBlocked, match="Exact Root provider schema capture hook"):
        backup.dump(
            "postgresql://unused@127.0.0.1/plantdb",
            "gateway",
            tmp_path,
            checkpoint=lambda: None,
            schema_capture=object(),
        )


def test_source_physical_authority_fence_blocks_before_transport(capture, monkeypatch):
    def changed_source(*_args, **_kwargs):
        raise ArchiveBlocked("Provider source identity differs")

    monkeypatch.setattr(schema_provider, "capture_provider", changed_source)
    with pytest.raises(ArchiveBlocked, match="source identity differs"):
        run(capture)
    assert not capture.target.exists()
    assert not (capture.folder / "response.json").exists()
