"""New provider format gates; synthetic files/SQLite, never native PG authority."""

import copy
import hashlib
import json
import secrets
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, text

from app.raw_archive.policy import ArchiveBlocked, checksum
from app.tiered_archive import backup, database_restore, native_backup
from app.tiered_archive import schema_provider as provider
from app.tiered_archive.codec import digest
from app.tiered_archive.pipeline import candidate_json

AT = datetime(2026, 11, 1, tzinfo=UTC)
SEQUENCE = {
    "public.fixture_seq": {"settings": [1, 1, 999, 1, 1, False], "last_value": 2, "is_called": True}
}
SHAPES = dict.fromkeys(
    (
        "columns",
        "constraints",
        "indexes",
        "views",
        "extensions",
        "hypertables",
        "chunks",
        "dimensions",
        "compression_settings",
    ),
    "a" * 64,
)


def toc_text(database, entries):
    return (
        ";     dbname: " + database + "\n;     Format: CUSTOM\n"
        ";     Dumped from database version: 15.10\n;     Dumped by pg_dump version: 15.10\n"
        + "\n".join(entries)
        + "\n"
    ).encode()


def scope(source="direct", *, production=False):
    database = (
        native_backup.PRODUCTION_SOURCE_DATABASES if production else native_backup.SOURCE_DATABASES
    )[source]
    schema_entries = ["2; 0 42 TABLE DATA _timescaledb_catalog hypertable owner"]
    masters_entries = [
        f"{10 + i}; 0 {50 + i} TABLE DATA public {name} owner"
        for i, name in enumerate(backup.master_tables(source))
    ]
    value = {
        "format": provider.SCOPE_FORMAT,
        "environment": "staging",
        "source": source,
        "database": database,
        "source_identity_sha256": "a" * 64,
        "postgres_major": 15,
        "extension": "timescaledb",
        "extension_version": "2.17.2",
        "config_relations": [
            {
                "schema": "_timescaledb_catalog",
                "name": "hypertable",
                "kind": "r",
                "oid": 42,
                "condition_sha256": hashlib.sha256(b"WHERE id >= 1").hexdigest(),
            }
        ],
        "schema_toc_sha256": digest(["1; 3079 100 EXTENSION - timescaledb -"]),
        "provider_toc_sha256": digest(schema_entries),
        "extensions": {
            "plpgsql": "1.0",
            "timescaledb": "2.17.2",
            **({"pgcrypto": "1.3", "uuid-ossp": "1.1"} if source == "gateway" else {}),
        },
        "masters_toc_sha256": digest(masters_entries),
        "master_sequences": [],
        "sequence_names": ["public.fixture_seq"],
    }
    if production:
        value.update(
            format=provider.PRODUCTION_SCOPE_FORMAT,
            environment="production",
            source_binding=copy.deepcopy(native_backup.PRODUCTION_SOURCE_BINDINGS[source]),
        )
    return value, toc_text(database, schema_entries), toc_text(database, masters_entries)


def test_provider_data_is_kept_and_public_data_never_authorized():
    value, schema, masters = scope()
    assert provider.validate_toc(schema, value, "provider", backup.DIRECT_MASTER_TABLES) == [
        "2; 0 42 TABLE DATA _timescaledb_catalog hypertable owner"
    ]
    assert provider.validate_toc(masters, value, "masters", backup.DIRECT_MASTER_TABLES) == []
    entries = provider.toc(schema, database=value["database"])
    entries.append("3; 0 55 TABLE DATA public sensor_reading owner")
    value["provider_toc_sha256"] = digest(
        entries
    )  # Even a matching Root TOC pin cannot waive the data scope.
    with pytest.raises(ArchiveBlocked, match="Unexpected schema DATA"):
        provider.validate_toc(
            toc_text(value["database"], entries), value, "provider", backup.DIRECT_MASTER_TABLES
        )


@pytest.mark.parametrize(
    "change", ["filter", "version", "unknown_namespace", "duplicate", "postgres"]
)
def test_unknown_provider_scope_never_defaults_to_supported(change):
    value, _, _ = scope()
    if change == "filter":
        value["config_relations"][0]["condition_sha256"] = "arbitrary-source-SQL"
    if change == "version":
        value["extension_version"] = "2.18"
    if change == "unknown_namespace":
        value["config_relations"][0]["schema"] = "public"
    if change == "duplicate":
        value["config_relations"].append(dict(value["config_relations"][0]))
    if change == "postgres":
        value["postgres_major"] = 16
    with pytest.raises(ArchiveBlocked):
        provider.validate_scope(value)


@pytest.mark.parametrize(
    "change",
    ["pin", "omit", "wrong_oid", "public_data", "duplicate", "database", "version", "database_toc"],
)
def test_toc_pin_order_required_entries_provider_oid_and_header(change):
    value, schema, _ = scope()
    entries = provider.toc(schema, database=value["database"])
    if change == "pin":
        value["provider_toc_sha256"] = "f" * 64
    if change == "omit":
        entries.pop()
    if change == "wrong_oid":
        entries[0] = entries[0].replace("0 42", "0 43")
    if change == "public_data":
        entries[0] = "2; 0 42 TABLE DATA public users owner"
    if change == "duplicate":
        entries.append(entries[0])
    if change == "database_toc":
        entries.append("3; 1262 9 DATABASE - unrelated owner")
    if change != "pin":
        value["provider_toc_sha256"] = digest(entries)
    raw = toc_text(value["database"], entries)
    if change == "database":
        raw = raw.replace(value["database"].encode(), b"production")
    if change == "version":
        raw = raw.replace(b"15.10", b"16.0")
    with pytest.raises(ArchiveBlocked):
        provider.validate_toc(raw, value, "provider", backup.DIRECT_MASTER_TABLES)


def test_master_dump_cannot_omit_or_add_nonmaster_data_even_with_new_pin():
    value, _, masters = scope()
    entries = provider.toc(masters, database=value["database"])
    entries.pop()
    value["masters_toc_sha256"] = digest(entries)
    with pytest.raises(ArchiveBlocked, match="omitted"):
        provider.validate_toc(
            toc_text(value["database"], entries), value, "masters", backup.DIRECT_MASTER_TABLES
        )
    entries.append("25; 0 100 TABLE DATA public direct_budget owner")
    value["masters_toc_sha256"] = digest(entries)
    with pytest.raises(ArchiveBlocked, match="Unexpected master"):
        provider.validate_toc(
            toc_text(value["database"], entries), value, "masters", backup.DIRECT_MASTER_TABLES
        )


def make_source(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "original.sqlite"))
    with engine.begin() as db:
        for name in backup.DIRECT_MASTER_TABLES:
            db.execute(
                text(f'CREATE TABLE "{name}"(id INTEGER PRIMARY KEY, payload TEXT NOT NULL)')
            )
            db.execute(text(f'INSERT INTO "{name}" VALUES(1,:value)'), {"value": name})
        legacy = backup.master_proof(db, "direct", checkpoint=lambda: None)
    return engine, legacy


def new_bundle(tmp_path, monkeypatch, *, production=False, schema_capture=None):
    original, legacy = make_source(tmp_path)
    root = tmp_path / "new-provider"
    root.mkdir(mode=0o700)
    value, schema_toc, masters_toc = scope(production=production)
    proof = {
        **legacy,
        "version": 4,
        "provider": {
            "scope": value,
            "structure": dict(SHAPES),
            "jobs_sha256": "b" * 64,
            "sequences": copy.deepcopy(SEQUENCE),
        },
    }

    def fake_dump(*args, **kwargs):
        assert kwargs["provider_scope"] == value and kwargs["with_proof"] is True
        if schema_capture is None:
            assert "schema_capture" not in kwargs
        else:
            assert kwargs["schema_capture"] is schema_capture
        paths = [root / "schema.dump", root / "masters.dump", root / "provider.dump"]
        for p in paths:
            p.write_bytes(b"synthetic authenticated archive")
        return paths, proof, AT

    monkeypatch.setattr(backup, "dump", fake_dump)
    monkeypatch.setattr(provider.os, "geteuid", lambda: 0)
    # Local fixture only: desktop user owns tmp_path. No native root ownership claim.
    original_stat = type(root).stat

    def fixture_stat(path, *a, **k):
        v = original_stat(path, *a, **k)
        if path == root:
            return SimpleNamespace(st_uid=0, st_mode=v.st_mode)
        return v

    monkeypatch.setattr(type(root), "stat", fixture_stat)
    key = secrets.token_bytes(32)
    candidate, files = provider.capture(
        (
            "postgresql://greenmind_archive_reader_33fe395:synthetic@127.0.0.1:5432/greenmind_direct_production"
            if production
            else "postgresql://greenmind_archive_staging_ro:synthetic@127.0.0.1:5433/greenmind_direct_staging"
        ),
        "direct",
        root,
        key,
        value,
        checkpoint=lambda: None,
        schema_capture=schema_capture,
    )
    manifest = {
        "candidate": candidate_json(candidate),
        "identity": candidate.identity,
        "files": [ref for _, ref in files],
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    return original, root, checksum(root / "manifest.json"), key, proof, schema_toc, masters_toc


def test_public_capture_forwards_only_explicit_schema_hook_into_existing_dump(
    tmp_path, monkeypatch
):
    hook = object()  # Only tests dispatch: backup.dump owns actual hook admission.
    original, root, _, _, _, _, _ = new_bundle(
        tmp_path, monkeypatch, production=True, schema_capture=hook
    )
    try:
        assert len(list(root.glob("000*.bin"))) == 4
    finally:
        original.dispose()


def test_capture_direct4_authenticated_header_and_no_silent_direct3_reinterpretation(
    tmp_path, monkeypatch
):
    original, root, sha, key, proof, _, _ = new_bundle(tmp_path, monkeypatch)
    try:
        assert sorted(p.name for p in root.glob("000*.bin")) == [
            "0001.bin",
            "0002.bin",
            "0003.bin",
            "0004.bin",
        ]
        assert backup.validate_master_proof(proof, "direct") == backup.DIRECT_MASTER_TABLES
        broken = copy.deepcopy(proof)
        broken["version"] = 3
        with pytest.raises(ArchiveBlocked):
            provider.validate_proof(broken, "direct")
        monkeypatch.setattr(
            database_restore, "run_client", lambda *a, **k: pytest.fail("No target authority")
        )
        with pytest.raises(ArchiveBlocked, match="supervisor"):
            database_restore.restore_backup(
                root,
                "postgresql://restore@127.0.0.1/gm_restore_new",
                sha,
                key,
                checkpoint=lambda: None,
            )
    finally:
        original.dispose()


@pytest.mark.parametrize("production", [False, True])
@pytest.mark.parametrize("post_change", [None, "master", "jobs", "unfinished"])
def test_direct4_full_capture_auth_data_order_typed_readback_and_handshake(
    tmp_path, monkeypatch, post_change, production
):
    original, root, sha, key, proof, schema_toc, masters_toc = new_bundle(
        tmp_path, monkeypatch, production=production
    )
    target = create_engine("sqlite:///" + str(tmp_path / "target.sqlite"))
    active = {"restoring": "on", "jobs": 0, "post_called": 0}
    commands = []
    from sqlalchemy.engine import Connection

    original_options = Connection.execution_options

    def local_sqlite_options(self, **kwargs):
        if self.dialect.name == "sqlite" and kwargs.get("isolation_level") == "REPEATABLE READ":
            kwargs["isolation_level"] = "SERIALIZABLE"
        return original_options(self, **kwargs)

    monkeypatch.setattr(Connection, "execution_options", local_sqlite_options)
    monkeypatch.setattr(database_restore, "_provider_target_identity", lambda *a: None)

    @event.listens_for(target, "connect")
    def functions(connection, record):
        connection.create_function("setval", 3, lambda *args: 1)

    @event.listens_for(target, "before_cursor_execute", retval=True)
    def control(conn, cursor, statement, params, context, many):
        if statement == "SET TRANSACTION READ ONLY":
            return "SELECT 1", ()
        if statement == "SHOW timescaledb.restoring":
            return "SELECT '" + active["restoring"] + "'", ()
        if statement == "SELECT count(*) FROM timescaledb_information.jobs WHERE scheduled":
            return "SELECT " + str(active["jobs"]), ()
        return statement, params

    def run(command, env, *, checkpoint, output=None):
        commands.append(command)
        assert env["PGDATABASE"] == "gm_restore_new" and "PGSERVICE" not in env
        if "--list" in command:
            if command[-1].endswith("schema.dump"):
                output.write_bytes(
                    toc_text(
                        proof["provider"]["scope"]["database"],
                        ["1; 3079 100 EXTENSION - timescaledb -"],
                    )
                )
            else:
                output.write_bytes(
                    schema_toc if command[-1].endswith("provider.dump") else masters_toc
                )
        elif output is not None:
            output.write_bytes(b"-- synthetic generated SQL\n")
        else:
            assert "--single-transaction" in command
            ordered = [command[i + 1] for i, value in enumerate(command[:-1]) if value == "--file"]
            assert [__import__("pathlib").Path(v).name for v in ordered] == [
                "guard.sql",
                "pre.sql",
                "provider.sql",
                "masters.sql",
                "post.sql",
            ]
            # Exact SQL control/TOC planning is real; PostgreSQL replay is explicitly a double.
            with target.begin() as db:
                for name in backup.DIRECT_MASTER_TABLES:
                    db.execute(
                        text(
                            f'CREATE TABLE "{name}"(id INTEGER PRIMARY KEY, payload TEXT NOT NULL)'
                        )
                    )
                    db.execute(text(f'INSERT INTO "{name}" VALUES(1,:value)'), {"value": name})

    monkeypatch.setattr(database_restore, "run_client", run)
    monkeypatch.setattr(database_restore, "create_engine", lambda *a, **k: target)
    monkeypatch.setattr(native_backup, "sequence_proof", lambda *a, **k: copy.deepcopy(SEQUENCE))
    monkeypatch.setattr(native_backup, "structure_proof", lambda *a, **k: dict(SHAPES))
    monkeypatch.setattr(native_backup, "jobs_proof", lambda *a, **k: "b" * 64)

    def post(*, checkpoint, evidence):
        assert evidence["source_jobs_sha256"] == evidence["restored_jobs_sha256"] == "b" * 64
        assert evidence["manifest_sha256"] == sha
        active.update(
            restoring="off" if post_change != "unfinished" else "on",
            post_called=1,
            jobs=1 if post_change == "jobs" else 0,
        )
        if post_change == "master":
            with target.begin() as db:
                db.execute(text("UPDATE direct_device SET payload='corrupted-after-proof'"))
        checkpoint()

    authority = {
        "coordinator": "existing-native-timescale-restore-v1",
        "database": "gm_restore_new",
        "oid": 9,
        "owner_oid": 10,
        "marker_sha256": "a" * 64,
    }
    try:

        def restore():
            return database_restore.restore_backup(
                root,
                "postgresql://restore@127.0.0.1/gm_restore_new",
                sha,
                key,
                checkpoint=lambda: None,
                provider_target=authority,
                post_restore=post,
            )

        if post_change is not None:
            with pytest.raises(ArchiveBlocked, match="post-restore"):
                restore()
            return
        result = restore()
        assert result["master_proof_version"] == 4 and result["typed_values_verified"] is True
        assert result["schema_provider_data_replayed"] == 1 and active["post_called"] == 1
        assert result["production_acceptance"] is False
        generated = [c for c in commands if any(v == "--section=data" for v in c)]
        assert len(generated) == 2
        assert any(v.startswith("--use-list=") for v in generated[0])
        assert not any(v.startswith("--use-list=") for v in generated[1])
    finally:
        original.dispose()
        target.dispose()


@pytest.mark.parametrize("change", ["ciphertext", "wrong_key", "proof_version", "source"])
def test_provider_auth_mismatch_never_opens_target_or_generates_sql(tmp_path, monkeypatch, change):
    original, root, sha, key, proof, _, _ = new_bundle(tmp_path, monkeypatch)
    authority = {
        "coordinator": "existing-native-timescale-restore-v1",
        "database": "gm_restore_new",
        "oid": 9,
        "owner_oid": 10,
        "marker_sha256": "a" * 64,
    }
    if change == "wrong_key":
        key = secrets.token_bytes(32)
    if change == "ciphertext":
        path = root / "0004.bin"
        data = bytearray(path.read_bytes())
        data[-1] ^= 1
        path.write_bytes(data)
    if change in {"proof_version", "source"}:
        # Manifest/hash may not authorize a different independently bound index.
        manifest = json.loads((root / "manifest.json").read_bytes())
        manifest["candidate"]["source"] = "gateway" if change == "source" else "direct"
        manifest["files"][0]["master_proof_version"] = 3
        (root / "manifest.json").write_text(json.dumps(manifest))
        sha = checksum(root / "manifest.json")
    monkeypatch.setattr(
        database_restore,
        "run_client",
        lambda *a, **k: pytest.fail("Unverified provider SQL generated"),
    )
    monkeypatch.setattr(
        database_restore, "create_engine", lambda *a, **k: pytest.fail("Unverified target opened")
    )
    try:
        with pytest.raises(ArchiveBlocked):
            database_restore.restore_backup(
                root,
                "postgresql://restore@127.0.0.1/gm_restore_new",
                sha,
                key,
                checkpoint=lambda: None,
                provider_target=authority,
                post_restore=lambda **k: None,
            )
    finally:
        original.dispose()


def test_target_identity_checks_actual_oid_marker_principal_acl_and_phase():
    from sqlalchemy.engine import make_url

    target = {
        "coordinator": "existing-native-timescale-restore-v1",
        "database": "gm_restore_new",
        "oid": 9,
        "owner_oid": 10,
        "marker_sha256": "a" * 64,
    }
    actual = {
        "database": "gm_restore_new",
        "oid": 9,
        "owner_oid": 10,
        "actor_oid": 10,
        "session_oid": 10,
        "marker_sha256": "a" * 64,
        "privileged": False,
        "public_connect": False,
        "public_temp": False,
        "restoring": "on",
    }

    class DB:
        def execute(self, statement):
            assert "shobj_description" in str(statement)
            return SimpleNamespace(mappings=lambda: SimpleNamespace(one=lambda: actual))

    url = make_url("postgresql://restore@127.0.0.1/gm_restore_new")
    database_restore._provider_target_identity(DB(), target, url, "on")
    for field in actual:
        previous = actual[field]
        actual[field] = not previous if type(previous) is bool else "changed"
        with pytest.raises(ArchiveBlocked, match="identity or phase"):
            database_restore._provider_target_identity(DB(), target, url, "on")
        actual[field] = previous


def test_target_sql_guard_is_atomic_and_never_waives_private_or_empty_database(monkeypatch):
    from sqlalchemy.engine import make_url

    monkeypatch.setattr(database_restore.os, "geteuid", lambda: 0)
    target = {
        "coordinator": "existing-native-timescale-restore-v1",
        "database": "gm_restore_new",
        "oid": 9,
        "owner_oid": 10,
        "marker_sha256": "a" * 64,
    }
    guard = database_restore._provider_target_guard(
        target, make_url("postgresql://restore@127.0.0.1/gm_restore_new")
    )
    assert "pg_advisory_xact_lock" in guard and "has_database_privilege" in guard
    assert "shobj_description" in guard and "current_user <> session_user" in guard
    assert "pg_class" in guard and "pg_proc" in guard and "deptype='e'" in guard
    assert "timescaledb.restoring" in guard
    bad = dict(target, database="plantdb_staging")
    with pytest.raises(ArchiveBlocked, match="authority"):
        database_restore._provider_target_guard(
            bad, make_url("postgresql://restore@127.0.0.1/gm_restore_new")
        )


@pytest.mark.parametrize("production", [False, True])
def test_provider_producer_both_dumps_share_one_snapshot_and_keep_extconfig_data(
    tmp_path, monkeypatch, production
):
    from pathlib import Path

    original, legacy = make_source(tmp_path)
    value, schema_toc, masters_toc = scope(production=production)
    calls = []
    metadata_checks = []
    event.listen(
        original,
        "before_cursor_execute",
        lambda c, cur, stmt, par, ctx, many: ("SELECT 1", ())
        if stmt.startswith("SET ")
        else ("SELECT 'single-exported-snapshot'", ())
        if stmt == "SELECT pg_export_snapshot()"
        else ("SELECT '2026-11-01T00:00:00+00:00'", ())
        if stmt == "SELECT transaction_timestamp()"
        else (stmt, par),
        retval=True,
    )
    fake = SimpleNamespace(
        connect=lambda: SimpleNamespace(
            execution_options=lambda **k: original.connect().execution_options(
                isolation_level="SERIALIZABLE"
            )
        ),
        dispose=lambda: None,
    )
    monkeypatch.setattr("sqlalchemy.create_engine", lambda *a, **k: fake)
    monkeypatch.setattr(provider.os, "geteuid", lambda: 0)
    monkeypatch.setenv("PGSERVICE", "must-not-inherit-another-database")
    monkeypatch.setattr(
        provider, "capture_provider", lambda db, sc, **k: metadata_checks.append(sc)
    )
    monkeypatch.setattr(native_backup, "structure_proof", lambda *a, **k: dict(SHAPES))
    monkeypatch.setattr(native_backup, "jobs_proof", lambda *a, **k: "b" * 64)
    monkeypatch.setattr(native_backup, "sequence_proof", lambda *a, **k: copy.deepcopy(SEQUENCE))

    class Process:
        returncode = 0

        def __init__(self, command, **kwargs):
            calls.append(command)
            assert "PGSERVICE" not in kwargs["env"]
            assert "default_transaction_read_only=on" in kwargs["env"]["PGOPTIONS"]
            assert kwargs["env"]["PGHOST"] == "127.0.0.1"
            assert kwargs["env"]["PGPORT"] == ("5432" if production else "5433")
            assert kwargs["env"]["PGDATABASE"] == value["database"]
            assert kwargs["env"]["PGUSER"] == (
                "greenmind_archive_reader_33fe395" if production else "greenmind_archive_staging_ro"
            )
            if "--list" in command:
                output = Path(next(c.split("=", 1)[1] for c in command if c.startswith("--file=")))
                if command[-1].endswith("schema.dump"):
                    output.write_bytes(
                        toc_text(value["database"], ["1; 3079 100 EXTENSION - timescaledb -"])
                    )
                else:
                    output.write_bytes(
                        schema_toc if command[-1].endswith("provider.dump") else masters_toc
                    )
            else:
                Path(command[command.index("--file") + 1]).write_bytes(b"synthetic PG dump")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def poll(self):
            return 0

    monkeypatch.setattr(backup.subprocess, "Popen", Process)
    output = tmp_path / "capture"
    output.mkdir(mode=0o700)
    try:
        files, new_proof, captured = backup.dump(
            (
                "postgresql://greenmind_archive_reader_33fe395:synthetic@127.0.0.1:5432/greenmind_direct_production"
                if production
                else "postgresql://greenmind_archive_staging_ro:synthetic@127.0.0.1:5433/greenmind_direct_staging"
            ),
            "direct",
            output,
            checkpoint=lambda: None,
            with_proof=True,
            provider_scope=value,
        )
        assert len(files) == 3 and new_proof["version"] == 4 and captured == AT
        dump_commands = [c for c in calls if "--snapshot" in c]
        assert len(dump_commands) == 3
        assert all(
            c[c.index("--snapshot") + 1] == "single-exported-snapshot" for c in dump_commands
        )
        assert "--schema-only" in dump_commands[0] and "--data-only" in dump_commands[1]
        assert len([c for c in calls if "--list" in c]) == 3
        assert "--no-blobs" in dump_commands[2] and "--strict-names" in dump_commands[2]
        assert not any(arg.startswith("--extension") for arg in dump_commands[2])
        selected = [
            dump_commands[2][i + 1]
            for i, arg in enumerate(dump_commands[2][:-1])
            if arg == "--table"
        ]
        assert selected == ['"_timescaledb_catalog"."hypertable"']
        assert metadata_checks == [value, value]
    finally:
        original.dispose()


def test_gateway5_is_explicit_and_keeps_v4_auth_absence_semantics(tmp_path, monkeypatch):
    original = create_engine("sqlite:///" + str(tmp_path / "gateway.sqlite"))
    with original.begin() as db:
        for name in backup.MASTER_TABLES:
            if name in {"access_invitation", "auth_session"}:
                continue
            if name == "alembic_version":
                db.execute(text("CREATE TABLE alembic_version(version_num TEXT PRIMARY KEY)"))
                db.execute(text("INSERT INTO alembic_version VALUES('0023')"))
            else:
                db.execute(
                    text(f'CREATE TABLE "{name}"(id INTEGER PRIMARY KEY,payload TEXT NOT NULL)')
                )
                db.execute(text(f'INSERT INTO "{name}" VALUES(1,:value)'), {"value": name})
        legacy = backup.master_proof(db, "gateway", proof_version=4, checkpoint=lambda: None)
    try:
        value, _, _ = scope("gateway")
        proof = {
            **legacy,
            "version": 5,
            "provider": {
                "scope": value,
                "structure": dict(SHAPES),
                "jobs_sha256": "b" * 64,
                "sequences": copy.deepcopy(SEQUENCE),
            },
        }
        assert backup.validate_master_proof(proof, "gateway") == backup.master_tables(
            "gateway", proof_version=4, schema_scope=legacy["schema_scope"]
        )
        assert provider.legacy_proof(proof, "gateway") == legacy
        assert (
            provider.legacy_proof(proof, "gateway")["schema_scope"]["mode"]
            == "schema0023-auth-absent"
        )
        # A new authenticated provider proof never passes as old V4.
        proof["version"] = 4
        with pytest.raises(ArchiveBlocked):
            backup.validate_master_proof(proof, "gateway")
    finally:
        original.dispose()


def test_provider_descriptor_is_versioned_source_specific_and_auth_file_bound(
    tmp_path, monkeypatch
):
    original, root, sha, key, proof, _, _ = new_bundle(tmp_path, monkeypatch)
    try:
        files = json.loads((root / "manifest.json").read_bytes())["files"]
        assert provider.current_descriptor(files, "direct") is True
        assert provider.current_descriptor(files, "gateway") is False
        assert provider.current_descriptor(files[:3], "direct") is False
        changed = copy.deepcopy(files)
        changed[0]["master_proof_version"] = 3
        assert provider.current_descriptor(changed, "direct") is False
        changed = copy.deepcopy(files)
        changed[4]["plaintext_sha256"] = "c" * 64
        assert provider.current_descriptor(changed, "direct") is False
    finally:
        original.dispose()


def test_provider_runtime_is_inert_for_unprivileged_or_production_before_worker(monkeypatch):
    from app.tiered_archive import runtime

    monkeypatch.setattr(runtime.os, "geteuid", lambda: 999)
    monkeypatch.setattr(
        runtime, "worker", lambda *a, **k: pytest.fail("Unprivileged provider job started")
    )
    with pytest.raises(ArchiveBlocked, match="root-only"):
        runtime.backup("direct", backup_format=provider.FORMAT)
    monkeypatch.setattr(runtime.os, "geteuid", lambda: 0)
    monkeypatch.setattr(runtime, "environment", lambda: "production")
    with pytest.raises(ArchiveBlocked, match="root-only"):
        runtime.backup("gateway", backup_format=provider.FORMAT)


def test_pg15_extension_sequence_set_uses_nil_catalog_oid_without_losing_source_identity():
    value, raw, _ = scope()
    row = {
        "schema": "_timescaledb_catalog",
        "name": "hypertable_id_seq",
        "kind": "S",
        "oid": 43,
        "condition_sha256": hashlib.sha256(b"").hexdigest(),
    }
    value["config_relations"].append(row)
    value["sequence_names"] = sorted(
        ["public.fixture_seq", "_timescaledb_catalog.hypertable_id_seq"]
    )
    entries = provider.toc(raw, database=value["database"])
    entries.append("3; 0 0 SEQUENCE SET _timescaledb_catalog hypertable_id_seq owner")
    value["provider_toc_sha256"] = digest(entries)
    chosen = provider.validate_toc(
        toc_text(value["database"], entries), value, "provider", backup.DIRECT_MASTER_TABLES
    )
    assert len(chosen) == 2 and chosen[-1].startswith("3; 0 0")
    # pg_dump.c dumpSequenceData uses nilCatalogId; a fabricated nonzero oid
    # must not be accepted as another relation identity.
    entries[-1] = entries[-1].replace("0 0 SEQUENCE", "0 43 SEQUENCE")
    value["provider_toc_sha256"] = digest(entries)
    with pytest.raises(ArchiveBlocked):
        provider.validate_toc(
            toc_text(value["database"], entries), value, "provider", backup.DIRECT_MASTER_TABLES
        )


@pytest.mark.parametrize(
    "data",
    [
        "TABLE DATA public private_rows owner",
        "TABLE DATA _timescaledb_catalog hypertable owner",
        "SEQUENCE SET _timescaledb_catalog hypertable_id_seq owner",
    ],
)
def test_legacy_schema_data_is_held_before_database_effects(tmp_path, monkeypatch, data):
    path = tmp_path / "schema.dump"
    path.write_bytes(b"synthetic authenticated old schema")

    def client(command, env, *, checkpoint, output=None):
        assert "--list" in command
        output.write_bytes(("; preserved legacy artifact\n1; 0 42 " + data + "\n").encode())

    monkeypatch.setattr(database_restore, "run_client", client)
    with pytest.raises(ArchiveBlocked, match="Legacy schema DATA requires"):
        database_restore._legacy_schema_without_data(path, {}, checkpoint=lambda: None)
    assert path.read_bytes() == b"synthetic authenticated old schema"


def test_legacy_timescale_without_data_cannot_claim_plain_pg_restore(tmp_path, monkeypatch):
    path = tmp_path / "schema.dump"
    path.write_bytes(b"preserved old TS schema")

    def client(command, env, *, checkpoint, output=None):
        assert "--list" in command
        output.write_bytes(
            b"; no DATA still lacks TS state\n1; 3079 42 EXTENSION - timescaledb owner\n"
        )

    monkeypatch.setattr(database_restore, "run_client", client)
    with pytest.raises(ArchiveBlocked, match="Legacy Timescale schema requires"):
        database_restore._legacy_schema_without_data(path, {}, checkpoint=lambda: None)
    assert path.read_bytes() == b"preserved old TS schema"
