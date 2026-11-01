"""V4 absence is catalogued, authenticated and independently checked on restore."""

import copy
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, text

from app.raw_archive.policy import ArchiveBlocked, checksum
from app.tiered_archive import backup, backup_scope, database_restore, parquet, runtime
from app.tiered_archive.codec import decode, digest, encode
from app.tiered_archive.pipeline import candidate_json
from tests.test_tiered_archive import AT
from tests.test_tiered_database_restore import URL, package


@pytest.fixture
def original(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "source.sqlite"))
    with engine.begin() as db:
        for name in backup.MASTER_TABLES:
            if name in backup_scope.AUTH_TABLES:
                continue
            if name == "alembic_version":
                db.execute(text("CREATE TABLE alembic_version(version_num TEXT PRIMARY KEY)"))
                db.execute(text("INSERT INTO alembic_version VALUES('0023')"))
            else:
                db.execute(
                    text(f"CREATE TABLE {name}(id INTEGER PRIMARY KEY,payload BLOB NOT NULL)")
                )
                db.execute(text(f"INSERT INTO {name} VALUES(1,X'0102')"))
    yield engine
    engine.dispose()


def proof(engine):
    with engine.connect() as db:
        return backup.master_proof(db, "gateway", proof_version=4, checkpoint=lambda: None)


def rebuild(folder, manifest, body):
    row = dict(
        identity=digest(body),
        organization="00000000-0000-0000-0000-000000000000",
        sensor="system",
        time_us=1,
        body=body,
    )
    target = folder / "replacement.parquet"
    descriptor = backup.v4_descriptor(body["master_proof"], body["files"])
    primary = dict(parquet.write(target, [row]), format="parquet", **descriptor)
    target.replace(folder / "0000.parquet")
    manifest["files"] = [primary, *body["files"]]
    candidate = replace(
        runtime.candidate_from_json(manifest["candidate"]), content_sha256=primary["content_sha256"]
    )
    manifest.update(candidate=candidate_json(candidate), identity=candidate.identity)
    (folder / "manifest.json").write_text(json.dumps(manifest))
    return checksum(folder / "manifest.json")


def bundle(tmp_path, engine):
    p = proof(engine)
    raw = engine.raw_connection()
    try:
        script = "\n".join(raw.driver_connection.iterdump()).encode()
    finally:
        raw.close()
    folder, _, key = package(tmp_path, proof_version=4, proof=p, dump_bytes=script)
    manifest = json.loads((folder / "manifest.json").read_text())
    body = list(parquet.read(folder / "0000.parquet", manifest["files"][0]))[0]["body"]
    path, reference = backup.seal_master_proof(
        folder, p, "gateway", AT, body["files"], key, checkpoint=lambda: None
    )
    path.replace(folder / "0003.bin")
    body["files"].append(reference)
    body["backup_format"] = backup_scope.FORMAT
    sha = rebuild(folder, manifest, body)
    return folder, manifest, body, key, sha


def test_exact_0023_absence_retains_form_users_and_all_existing_authority(original):
    p = proof(original)
    assert p["version"] == 4 and p["schema_scope"]["alembic_heads"] == ["0023"]
    assert p["schema_scope"]["mode"] == "schema0023-auth-absent"
    assert p["schema_scope"]["auth_relations"] == dict.fromkeys(backup_scope.AUTH_TABLES, "absent")
    assert len(p["tables"]) == 11 and {"form_submission", "users"} <= set(p["tables"])
    assert set(p["tables"]) == set(backup.MASTER_TABLES) - set(backup_scope.AUTH_TABLES)
    assert p != dict(version=3, tables=p["tables"])
    with pytest.raises(ArchiveBlocked, match="missing"):
        with original.connect() as db:
            backup.master_proof(db, "gateway", checkpoint=lambda: None)


@pytest.mark.parametrize("relation", ["auth_session", "access_invitation"])
def test_partial_auth_presence_fails_closed(original, relation):
    with original.begin() as db:
        db.execute(text(f"CREATE TABLE {relation}(id INTEGER PRIMARY KEY)"))
    with pytest.raises(ArchiveBlocked, match="both auth tables"):
        proof(original)


def test_auth_view_is_presence_not_a_proved_absence(original):
    with original.begin() as db:
        db.execute(text("CREATE VIEW auth_session AS SELECT id FROM users"))
    with pytest.raises(ArchiveBlocked, match="not a base table"):
        proof(original)


@pytest.mark.parametrize("heads", [[], ["0024"], ["0023", "0024"], ["unknown"]])
def test_missing_newer_or_ambiguous_heads_cannot_claim_absence(original, heads):
    with original.begin() as db:
        db.execute(text("DELETE FROM alembic_version"))
        for head in heads:
            db.execute(text("INSERT INTO alembic_version VALUES(:v)"), {"v": head})
    with pytest.raises(ArchiveBlocked):
        proof(original)


def test_full_auth_scope_has_thirteen_tables_and_detects_shape_changes(original):
    with original.begin() as db:
        for name in backup_scope.AUTH_TABLES:
            db.execute(text(f"CREATE TABLE {name}(id INTEGER PRIMARY KEY)"))
        db.execute(text("UPDATE alembic_version SET version_num='0024'"))
    before = proof(original)
    assert len(before["tables"]) == 13 and before["schema_scope"]["mode"] == "full-auth"
    with original.begin() as db:
        db.execute(text("ALTER TABLE auth_session ADD COLUMN revoked_at TEXT"))
    assert proof(original)["schema_scope"] != before["schema_scope"]


def test_schema_change_between_master_captures_is_never_silently_adopted(original):
    old = proof(original)["schema_scope"]
    with original.begin() as db:
        for name in backup_scope.AUTH_TABLES:
            db.execute(text(f"CREATE TABLE {name}(id INTEGER PRIMARY KEY)"))
    with original.connect() as db, pytest.raises(ArchiveBlocked, match="changed during capture"):
        backup.master_proof(
            db, "gateway", proof_version=4, schema_scope=old, checkpoint=lambda: None
        )


@pytest.mark.parametrize("change", ["mode", "head", "absence", "catalog", "missing_table"])
def test_manipulated_scope_or_missing_authority_rows_rejected(original, change):
    p = proof(original)
    if change == "mode":
        p["schema_scope"]["mode"] = "legacy"
    if change == "head":
        p["schema_scope"]["alembic_heads"] = ["0024"]
    if change == "absence":
        p["schema_scope"]["auth_relations"].pop("auth_session")
    if change == "catalog":
        p["schema_scope"]["catalog_sha256"] = "f" * 64
    if change == "missing_table":
        p["tables"].pop("users")
    with pytest.raises(ArchiveBlocked):
        backup.validate_master_proof(p, "gateway")


@pytest.mark.parametrize(
    "change", ["outer_proof", "source", "capture", "dump", "version", "ciphertext"]
)
def test_v4_forged_authenticated_bindings_never_reach_sql(original, tmp_path, monkeypatch, change):
    folder, manifest, body, key, sha = bundle(tmp_path, original)
    monkeypatch.setattr(database_restore, "run_client", lambda *a, **k: pytest.fail("SQL touched"))
    if change == "outer_proof":
        body["master_proof"]["tables"]["users"]["rows"] += 1
    elif change == "ciphertext":
        path = folder / "0003.bin"
        data = bytearray(path.read_bytes())
        data[-1] ^= 1
        path.write_bytes(data)
        body["files"][2]["sha256"] = checksum(path)
    else:
        plain = folder / "edit-auth"
        backup.decrypt(
            folder / "0003.bin",
            plain,
            key,
            body["files"][2]["plaintext_sha256"],
            checkpoint=lambda: None,
        )
        value = decode(plain.read_bytes())
        if change == "source":
            value["source"] = "direct"
        if change == "capture":
            value["captured_at"] = AT.replace(year=2027)
        if change == "dump":
            value["dump_files"][0]["plaintext_sha256"] = "f" * 64
        if change == "version":
            value["version"] = True
        plain.write_bytes(encode(value))
        reference = backup.encrypt(plain, folder / "replacement.bin", key, checkpoint=lambda: None)
        (folder / "replacement.bin").replace(folder / "0003.bin")
        plain.unlink()
        body["files"][2] = reference
    sha = rebuild(folder, manifest, body)
    with pytest.raises(ArchiveBlocked, match="authenticat|binding"):
        database_restore.restore_backup(folder, URL, sha, key, checkpoint=lambda: None)
    assert not list(folder.glob("restore-private-*"))


@pytest.mark.parametrize("mutation", ["none", "auth_created", "head_changed", "row_changed"])
def test_v4_restore_compares_actual_catalog_head_and_values(
    original, tmp_path, monkeypatch, mutation
):
    folder, _, _, key, sha = bundle(tmp_path, original)
    target = create_engine("sqlite:///" + str(tmp_path / "restored.sqlite"))

    def control(conn, cursor, statement, parameters, context, many):
        if statement.startswith("SET "):
            return "SELECT 1", ()
        return statement, parameters

    event.listen(target, "before_cursor_execute", control, retval=True)

    def run_client(command, env, *, checkpoint, output=None):
        if "--list" in command:
            output.write_bytes(
                b"; synthetic plain-PG schema TOC\n1; 1259 42 TABLE public fixture owner\n"
            )
        elif output is not None:
            output.write_bytes(Path(command[-1]).read_bytes())
        else:
            script = next(Path(v) for v in command if v.endswith("/masters.sql"))
            raw = target.raw_connection()
            try:
                raw.driver_connection.executescript(script.read_text())
                if mutation == "auth_created":
                    raw.driver_connection.execute(
                        "CREATE TABLE auth_session(id INTEGER PRIMARY KEY)"
                    )
                if mutation == "head_changed":
                    raw.driver_connection.execute("UPDATE alembic_version SET version_num='0024'")
                if mutation == "row_changed":
                    raw.driver_connection.execute("UPDATE users SET payload=X'ff'")
                raw.commit()
            finally:
                raw.close()

    def engine(*args, **kwargs):
        return SimpleNamespace(
            connect=lambda: SimpleNamespace(
                execution_options=lambda **k: target.connect().execution_options(
                    isolation_level="SERIALIZABLE"
                )
            ),
            dispose=lambda: None,
        )

    monkeypatch.setattr(database_restore, "run_client", run_client)
    monkeypatch.setattr(database_restore, "create_engine", engine)
    try:
        if mutation == "none":
            result = database_restore.restore_backup(folder, URL, sha, key, checkpoint=lambda: None)
            assert result["master_proof_version"] == 4 and result["master_tables"] == 11
            assert result["typed_values_verified"] and result["current_recovery_scope"]
        else:
            with pytest.raises(ArchiveBlocked):
                database_restore.restore_backup(folder, URL, sha, key, checkpoint=lambda: None)
    finally:
        target.dispose()


def test_current_gate_requires_real_v4_descriptor_and_four_files(original, tmp_path):
    _, manifest, _, _, _ = bundle(tmp_path, original)
    from app.tiered_archive.policy import POLICY_SHA256

    receipt = dict(
        state="verified",
        policy=POLICY_SHA256,
        destination="box",
        files=manifest["files"],
        source=dict(
            dataset="database_backup",
            source="gateway",
            environment="production",
            end=AT.isoformat(),
        ),
        verified_at=AT.isoformat(),
        snapshot=dict(
            provider_id=1, box_id=2, created_at=AT.isoformat(), checked_at=AT.isoformat()
        ),
    )
    arguments = dict(source="gateway", environment="production", destination="box", now=AT)
    with pytest.raises(ArchiveBlocked):
        backup.require_current([receipt], **arguments)
    live = copy.deepcopy(receipt["files"][0]["master_schema_scope"])
    arguments["schema_scope"] = lambda: live
    assert backup.require_current([receipt], **arguments) == receipt
    for kind in ("auth_file", "scope", "proof_version", "dump_binding"):
        changed = copy.deepcopy(receipt)
        if kind == "auth_file":
            changed["files"].pop()
        if kind == "scope":
            changed["files"][0]["master_schema_scope"]["alembic_heads"] = ["0024"]
        if kind == "proof_version":
            changed["files"][0]["master_proof_version"] = 2
        if kind == "dump_binding":
            changed["files"][1]["plaintext_sha256"] = "f" * 64
        with pytest.raises(ArchiveBlocked, match="backup required"):
            backup.require_current([changed], **arguments)
    live["alembic_heads"] = ["0024"]
    with pytest.raises(ArchiveBlocked):
        backup.require_current([receipt], **arguments)


def test_live_scope_reader_rejects_another_database_before_sql(monkeypatch):
    monkeypatch.setattr(runtime, "environment", lambda: "staging")
    monkeypatch.setenv("TIERED_ARCHIVE_GATEWAY_BACKUP_DATABASE_URL", URL)
    monkeypatch.setattr(
        "app.raw_archive.runner.metadata_source_settings",
        lambda *a: {"database": URL.replace("greenmind_restore_test", "another_source")},
    )
    monkeypatch.setattr(
        backup, "fresh_schema_scope", lambda *a, **k: pytest.fail("wrong source SQL")
    )
    with pytest.raises(ArchiveBlocked, match="another source"):
        runtime.current_backup_schema_scope("gateway", lambda: None)


@pytest.mark.parametrize("changed", [False, True])
def test_dump_uses_exact_11_tables_and_rejects_fresh_catalog_drift(
    original, tmp_path, monkeypatch, changed
):
    scope = proof(original)["schema_scope"]
    calls = []
    dumps = []

    def fresh(*args, **kwargs):
        calls.append(True)
        result = copy.deepcopy(scope)
        if changed and len(calls) > 1:
            result["alembic_heads"] = ["0024"]
            result["mode"] = "full-auth"
            result["auth_relations"] = dict.fromkeys(backup_scope.AUTH_TABLES, "r")
            result["auth_shapes"] = dict.fromkeys(backup_scope.AUTH_TABLES, "b" * 64)
            result["catalog_sha256"] = digest(
                {k: result[k] for k in ["schema", "auth_relations", "auth_shapes"]}
            )
            backup_scope.validate_scope(result)
        return result

    def control(conn, cursor, statement, parameters, context, many):
        if statement.startswith("SET "):
            return "SELECT 1", ()
        if statement == "SELECT pg_export_snapshot()":
            return "SELECT 'synthetic-exported-snapshot'", ()
        return statement, parameters

    event.listen(original, "before_cursor_execute", control, retval=True)

    class Process:
        returncode = 0

        def __init__(self, command, **kwargs):
            dumps.append(command)
            Path(command[command.index("--file") + 1]).write_bytes(b"synthetic-dump")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def poll(self):
            return 0

    engine = SimpleNamespace(
        connect=lambda: SimpleNamespace(
            execution_options=lambda **k: original.connect().execution_options(
                isolation_level="SERIALIZABLE"
            )
        ),
        dispose=lambda: None,
    )
    monkeypatch.setattr(backup, "fresh_schema_scope", fresh)
    monkeypatch.setattr("sqlalchemy.create_engine", lambda *args, **kwargs: engine)
    monkeypatch.setattr(backup.subprocess, "Popen", Process)
    target = tmp_path / "dump"
    target.mkdir()
    if changed:
        with pytest.raises(ArchiveBlocked, match="between captures"):
            backup.dump(URL, "gateway", target, checkpoint=lambda: None, with_proof=True)
    else:
        files, result = backup.dump(
            URL, "gateway", target, checkpoint=lambda: None, with_proof=True
        )
        assert len(files) == 2 and result["version"] == 4 and len(result["tables"]) == 11
    assert len(calls) == 2 and len(dumps) == 2
    selected = [dumps[1][i + 1] for i, arg in enumerate(dumps[1]) if arg == "--table"]
    assert (
        len(selected) == 11 and "public.form_submission" in selected and "public.users" in selected
    )
    assert not any("auth_session" in s or "access_invitation" in s for s in selected)


def test_runtime_publishes_actual_four_file_authenticated_v4(original, tmp_path, monkeypatch):
    import secrets
    from contextlib import contextmanager

    from app.tiered_archive.ledger import Ledger
    from tests.test_tiered_archive import Box

    p = proof(original)
    ledger = Ledger(tmp_path / "ledger")
    box = Box(tmp_path / "box")

    @contextmanager
    def worker():
        yield ledger, box, lambda: None

    def dumped(database, source, folder, *, checkpoint, with_proof):
        assert database == URL and source == "gateway" and with_proof
        files = [folder / "schema.dump", folder / "masters.dump"]
        for f in files:
            f.write_bytes(b"synthetic encrypted SQL dump")
        return files, p

    key = secrets.token_bytes(32)
    path = tmp_path / "key"
    path.write_bytes(key)
    path.chmod(0o600)
    monkeypatch.setenv("TIERED_ARCHIVE_GATEWAY_BACKUP_DATABASE_URL", URL)
    monkeypatch.setenv("TIERED_ARCHIVE_BACKUP_KEY_FILE", str(path))
    monkeypatch.setattr(
        "app.raw_archive.runner.metadata_source_settings", lambda *a: {"database": URL}
    )
    monkeypatch.setattr(runtime, "environment", lambda: "staging")
    monkeypatch.setattr(runtime, "worker", worker)
    monkeypatch.setattr(backup, "dump", dumped)
    try:
        receipt = runtime.backup("gateway")
        files = receipt["files"]
        assert len(files) == 4 and backup.current_v4_descriptor(files)
        body = list(parquet.read(box.root / files[0]["key"], files[0]))[0]["body"]
        plain = tmp_path / "read-auth"
        backup.decrypt(
            box.root / files[3]["key"],
            plain,
            key,
            files[3]["plaintext_sha256"],
            checkpoint=lambda: None,
        )
        bound = decode(plain.read_bytes())
        assert bound["source"] == "gateway" and bound["captured_at"] == body["at"]
        assert bound["master_proof"] == p and bound["dump_files"] == body["files"][:2]
        assert body["backup_format"] == backup_scope.FORMAT
        assert not list(ledger.root.glob("**/proof-auth.json"))
    finally:
        ledger.close()
