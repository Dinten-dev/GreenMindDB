"""Explicit production-provider interfaces; synthetic catalogues, no native calls."""

import copy
from datetime import UTC, datetime

import pytest

from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import native_backup
from app.tiered_archive import schema_provider as provider
from app.tiered_archive.codec import decode, digest
from tests.test_tiered_schema_provider import new_bundle, scope

URL = "postgresql://greenmind_archive_reader_33fe395:synthetic@127.0.0.1:5432/greenmind_direct_production"


@pytest.mark.parametrize("production", [False, True])
@pytest.mark.parametrize("source", ["gateway", "direct"])
def test_runtime_provider_requires_matching_reviewed_scope_before_worker(monkeypatch, production, source):
    from app.tiered_archive import runtime

    value, _, _ = scope(source, production=production)
    monkeypatch.setattr(runtime.os, "geteuid", lambda: 0)
    monkeypatch.setattr(runtime, "environment", lambda: value["environment"])
    monkeypatch.setenv("TIERED_ARCHIVE_SCHEMA_PROVIDER_SCOPE_FILE", "/private/reviewed-scope")
    monkeypatch.setenv("TIERED_ARCHIVE_SCHEMA_PROVIDER_SCOPE_SHA256", "a" * 64)

    def read_scope(path, pin):
        assert path == "/private/reviewed-scope" and pin == "a" * 64
        return provider.validate_scope(value)

    class ReviewedWorkerReached(Exception):
        pass

    def worker():
        raise ReviewedWorkerReached

    monkeypatch.setattr(provider, "read_scope", read_scope)
    monkeypatch.setattr(runtime, "worker", worker)
    with pytest.raises(ReviewedWorkerReached):
        runtime.backup(source, backup_format=provider.FORMAT)
    other_source = "gateway" if source == "direct" else "direct"
    with pytest.raises(ArchiveBlocked, match="another environment or source"):
        runtime.backup(other_source, backup_format=provider.FORMAT)
    monkeypatch.setattr(runtime, "environment", lambda: "staging" if production else "production")
    with pytest.raises(ArchiveBlocked, match="another environment or source"):
        runtime.backup(source, backup_format=provider.FORMAT)


def test_runtime_unreviewed_full_backup_still_cannot_run_in_production(monkeypatch):
    from app.tiered_archive import runtime

    monkeypatch.setattr(runtime, "environment", lambda: "production")
    monkeypatch.setattr(runtime, "worker", lambda: pytest.fail("Unreviewed full backup started"))
    with pytest.raises(ArchiveBlocked, match="restricted to Staging"):
        runtime.backup("direct", backup_format="postgres-full-v1")


@pytest.mark.parametrize("source", ["gateway", "direct"])
def test_explicit_production_contract_uses_existing_fixed_reader_only(source):
    value, _, _ = scope(source, production=True)
    assert provider.validate_scope(value) is value
    url = URL.replace("greenmind_direct_production", value["database"])
    result = native_backup.source_url(url, source, "production", provider_scope=value)
    assert result.port == 5432 and result.username == value["source_binding"]["role"]
    with pytest.raises(ArchiveBlocked, match="Staging"):
        native_backup.source_url(url, source, "production")


@pytest.mark.parametrize(
    "change",
    ["v1", "environment", "database", "source", "host", "port", "role", "oid", "session", "extra"],
)
def test_production_authority_never_relables_staging_or_other_principals(change):
    value, _, _ = scope(production=True)
    if change == "v1":
        value["format"] = provider.SCOPE_FORMAT
    elif change == "environment":
        value["environment"] = "staging"
    elif change == "database":
        value["database"] = "greenmind_direct_staging"
    elif change == "source":
        value["source"] = "gateway"
    elif change == "extra":
        value["allow_other_url"] = True
    else:
        key, replacement = {
            "host": ("host", "localhost"),
            "port": ("port", 6432),
            "role": ("role", "plantuser"),
            "oid": ("database_oid", 22774),
            "session": ("session_oid", 23751),
        }[change]
        value["source_binding"][key] = replacement
    with pytest.raises(ArchiveBlocked):
        provider.validate_scope(value)


@pytest.mark.parametrize(
    "missing", ["schema_toc_sha256", "masters_toc_sha256", "provider_toc_sha256"]
)
def test_independent_production_toc_pins_remain_mandatory(missing):
    value, _, _ = scope(production=True)
    value[missing] = None
    with pytest.raises(ArchiveBlocked, match="TOC pins"):
        provider.validate_scope(value)


@pytest.mark.parametrize(
    "url",
    [
        URL.replace("127.0.0.1", "localhost"),
        URL.replace(":5432", ":6432"),
        URL.replace("greenmind_archive_reader_33fe395", "plantuser"),
        URL.replace("greenmind_direct_production", "greenmind_direct_staging"),
        URL.replace(":synthetic@", "@"),
        URL.replace("postgresql:", "mysql:"),
        URL + "?host=another-server",
    ],
)
def test_source_url_rejects_every_unreviewed_binding(url):
    value, _, _ = scope(production=True)
    with pytest.raises(ArchiveBlocked):
        native_backup.source_url(url, "direct", "production", provider_scope=value)


def test_source_and_environment_cannot_cross_authority_or_full_v1(monkeypatch, tmp_path):
    value, _, _ = scope(production=True)
    for source, environment in (("gateway", "production"), ("direct", "staging")):
        with pytest.raises(ArchiveBlocked, match="authority differ"):
            native_backup.source_url(URL, source, environment, provider_scope=value)
    monkeypatch.setattr(native_backup, "create_engine", lambda *a, **k: pytest.fail("Before DB"))
    with pytest.raises(ArchiveBlocked, match="Staging"):
        native_backup.capture(
            URL, "direct", tmp_path, b"x" * 32, environment="production", checkpoint=lambda: None
        )


class Result:
    def __init__(self, value):
        self.value = value

    def mappings(self):
        return self

    def one(self):
        return copy.deepcopy(self.value)

    def all(self):
        return copy.deepcopy(self.value)

    def scalar_one(self):
        return self.value


class Catalog:
    def __init__(self, identity):
        self.identity = identity
        self.readonly = "on"
        self.unprivileged = True
        self.queries = []

    def execute(self, sql, params=None):
        command = str(sql)
        self.queries.append(command)
        if "pg_postmaster_start_time" in command:
            return Result(self.identity)
        if command == "SHOW transaction_read_only":
            return Result(self.readonly)
        if "rolcanlogin" in command:
            assert params == {"role": 499352, "name": "greenmind_archive_reader_33fe395"}
            return Result(self.unprivileged)
        if "server_version_num" in command:
            return Result(150010)
        if "SELECT extname,extversion" in command:
            return Result([("plpgsql", "1.0"), ("timescaledb", "2.17.2")])
        if "unnest(e.extconfig" in command:
            return Result(
                [
                    {
                        "schema": "_timescaledb_catalog",
                        "name": "hypertable",
                        "kind": "r",
                        "oid": 42,
                        "condition": "WHERE id >= 1",
                        "member": True,
                    }
                ]
            )
        raise AssertionError("Unexpected synthetic catalogue query")


def catalog():
    value, _, _ = scope(production=True)
    identity = {
        "database": value["database"],
        "oid": 301614,
        "address": "172.28.20.5/32",
        "port": 5432,
        "postmaster_start": datetime(2026, 8, 31, 16, 13, tzinfo=UTC),
        "actor": 499352,
        "session": 499352,
    }
    value["source_identity_sha256"] = digest(identity)
    return value, Catalog(identity)


def test_capture_uses_real_identity_readonly_principal_and_independent_provider_filter():
    value, db = catalog()
    assert provider.capture_provider(db, value, checkpoint=lambda: None) is value
    assert len(db.queries) == 6


@pytest.mark.parametrize(
    "change", ["address", "postmaster", "oid", "actor", "session", "readonly", "privileged"]
)
def test_live_physical_or_principal_drift_blocks_before_provider_rows(change):
    value, db = catalog()
    if change == "address":
        db.identity["address"] = "172.28.21.3/32"
    elif change == "postmaster":
        db.identity["postmaster_start"] = datetime(2026, 11, 1, tzinfo=UTC)
    elif change in {"oid", "actor", "session"}:
        db.identity[change] = 23751
        # Even a self-consistent physical digest cannot bypass the fixed role/OID contract.
        value["source_identity_sha256"] = digest(db.identity)
    elif change == "readonly":
        db.readonly = "off"
    else:
        db.unprivileged = False
    with pytest.raises(ArchiveBlocked):
        provider.capture_provider(db, value, checkpoint=lambda: None)
    assert not any("unnest(e.extconfig" in query for query in db.queries)


def test_new_production_header_is_aes_bound_and_not_a_staging_capture(tmp_path, monkeypatch):
    from app.tiered_archive import backup

    engine, root, _, key, proof, _, _ = new_bundle(tmp_path, monkeypatch, production=True)
    try:
        import json

        manifest = json.loads((root / "manifest.json").read_bytes())
        assert manifest["candidate"]["environment"] == "production"
        auth = root / "read-auth.json"
        backup.decrypt(
            root / "0004.bin",
            auth,
            key,
            manifest["files"][4]["plaintext_sha256"],
            checkpoint=lambda: None,
        )
        actual = decode(auth.read_bytes())
        assert actual["environment"] == "production" and actual["master_proof"] == proof
        assert (
            actual["master_proof"]["provider"]["scope"]["format"]
            == provider.PRODUCTION_SCOPE_FORMAT
        )
    finally:
        engine.dispose()


def test_production_proof_cannot_restore_as_staging_before_decrypt_or_target_io(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    from sqlalchemy.engine import make_url

    from app.tiered_archive import database_restore, parquet, runtime

    engine, root, sha, key, _, _, _ = new_bundle(tmp_path, monkeypatch, production=True)
    try:
        import json

        manifest = json.loads((root / "manifest.json").read_bytes())
        candidate = replace(
            runtime.candidate_from_json(manifest["candidate"]), environment="staging"
        )
        parts = manifest["files"]
        body = list(parquet.read(root / "0000.parquet", parts[0]))[0]["body"]
        monkeypatch.setattr(
            database_restore, "decrypt", lambda *a, **k: pytest.fail("Before decrypt")
        )
        target = {
            "coordinator": "existing-native-timescale-restore-v1",
            "database": "gm_restore_new",
            "oid": 9,
            "owner_oid": 10,
            "marker_sha256": "a" * 64,
        }
        with pytest.raises(ArchiveBlocked, match="descriptor differs"):
            database_restore._restore_provider(
                root,
                make_url("postgresql://restore@127.0.0.1/gm_restore_new"),
                sha,
                key,
                candidate,
                parts,
                body,
                provider_target=target,
                post_restore=lambda **k: None,
                checkpoint=lambda: None,
            )
    finally:
        engine.dispose()
