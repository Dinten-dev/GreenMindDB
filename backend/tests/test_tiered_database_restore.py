"""Authenticated backup bindings and typed master-data proofs without live services."""

import json
import secrets
import uuid
from datetime import UTC, timedelta

import pytest
from sqlalchemy import create_engine, text

from app.raw_archive.policy import ArchiveBlocked, checksum
from app.tiered_archive import backup, parquet
from app.tiered_archive.codec import digest
from app.tiered_archive.database_restore import restore_backup
from app.tiered_archive.pipeline import candidate_json
from app.tiered_archive.policy import Candidate
from tests.test_tiered_archive import AT

ORG = "00000000-0000-0000-0000-000000000000"
URL = "postgresql+psycopg2://restore@127.0.0.1/greenmind_restore_test"


def package(
    tmp_path,
    change=None,
    *,
    proof_version=backup.MASTER_PROOF_VERSION,
    proof=None,
    dump_bytes=b"synthetic authenticated dump",
):
    folder = tmp_path / "bundle"
    folder.mkdir()
    key = secrets.token_bytes(32)
    parts = []
    for index in (1, 2):
        plain = tmp_path / f"{index}.dump"
        plain.write_bytes(dump_bytes)
        parts.append(
            backup.encrypt(plain, folder / f"{index:04}.bin", key, checkpoint=lambda: None)
        )
    body = {
        "source": "gateway",
        "at": AT,
        "files": [dict(part) for part in parts],
        "master_proof": proof
        if proof is not None
        else {
            "version": proof_version,
            "tables": {
                table: {"rows": 0, "content_sha256": "a" * 64}
                for table in backup.master_tables("gateway", proof_version=proof_version)
            },
        },
    }
    if change:
        change(body, parts, folder)
    row = {
        "identity": digest(body),
        "organization": ORG,
        "sensor": "system",
        "time_us": 1,
        "body": body,
    }
    index = dict(
        parquet.write(folder / "0000.parquet", [row]),
        format="parquet",
        master_proof_version=proof_version,
    )
    candidate = Candidate(
        "staging",
        "gateway",
        "database_backup",
        ORG,
        AT - timedelta(seconds=1),
        AT,
        index["content_sha256"],
        1,
        "features",
        AT,
    )
    manifest = {
        "candidate": candidate_json(candidate),
        "identity": candidate.identity,
        "files": [index, *parts],
    }
    (folder / "manifest.json").write_text(json.dumps(manifest))
    return folder, checksum(folder / "manifest.json"), key


@pytest.fixture(autouse=True)
def no_database_clients(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid backup must not invoke PostgreSQL clients")

    monkeypatch.setattr("app.tiered_archive.database_restore.run_client", forbidden)


@pytest.mark.parametrize("proof_version", [2, 3])
def test_wrong_key_never_opens_database_and_cleans_plaintext(tmp_path, proof_version):
    folder, sha, _ = package(tmp_path, proof_version=proof_version)
    with pytest.raises(ArchiveBlocked, match="authentication"):
        restore_backup(folder, URL, sha, secrets.token_bytes(32), checkpoint=lambda: None)
    assert not list(folder.glob("restore-private-*"))


@pytest.mark.parametrize("proof_version", [2, 3])
def test_second_authenticated_record_failure_before_database(tmp_path, proof_version):
    def tamper(body, parts, folder):
        path = folder / "0002.bin"
        raw = bytearray(path.read_bytes())
        raw[-1] ^= 1
        path.write_bytes(raw)
        parts[1]["sha256"] = checksum(path)
        body["files"][1] = dict(parts[1])

    folder, sha, key = package(tmp_path, tamper, proof_version=proof_version)
    with pytest.raises(ArchiveBlocked, match="authentication"):
        restore_backup(folder, URL, sha, key, checkpoint=lambda: None)
    assert not list(folder.glob("restore-private-*"))


def test_index_and_dump_binding_cannot_diverge(tmp_path):
    def tamper(body, parts, folder):
        body["files"][1]["plaintext_sha256"] = "f" * 64

    folder, sha, key = package(tmp_path, tamper)
    with pytest.raises(ArchiveBlocked, match="index binding"):
        restore_backup(folder, URL, sha, key, checkpoint=lambda: None)


@pytest.mark.parametrize(
    "proof",
    [
        None,
        {"version": 2},
        {"version": backup.MASTER_PROOF_VERSION, "tables": {}},
        {
            "version": backup.MASTER_PROOF_VERSION,
            "tables": {name: {"rows": -1, "content_sha256": "x"} for name in backup.MASTER_TABLES},
        },
    ],
)
def test_missing_or_incomplete_proof_cannot_claim_full_restore(tmp_path, proof):
    folder, sha, key = package(
        tmp_path, lambda body, parts, folder: body.update(master_proof=proof)
    )
    with pytest.raises(ArchiveBlocked, match="proof"):
        restore_backup(folder, URL, sha, key, checkpoint=lambda: None)


def test_changed_manifest_blocks(tmp_path):
    folder, sha, key = package(tmp_path)
    (folder / "manifest.json").write_text("{}")
    with pytest.raises(ArchiveBlocked, match="manifest changed"):
        restore_backup(folder, URL, sha, key, checkpoint=lambda: None)


def test_hidden_target_override_blocks_before_reading_backup(tmp_path):
    with pytest.raises(ArchiveBlocked, match="dedicated local"):
        restore_backup(tmp_path, URL + "?host=production", "a" * 64, b"", checkpoint=lambda: None)


@pytest.fixture
def masters(tmp_path, request):
    engine = create_engine("sqlite:///" + str(tmp_path / "masters.sqlite"))
    with engine.begin() as db:
        for name in backup.master_tables("gateway", proof_version=getattr(request, "param", 3)):
            db.execute(
                text(f'CREATE TABLE "{name}"(id INTEGER PRIMARY KEY, payload BLOB NOT NULL)')
            )
            db.execute(
                text(f'INSERT INTO "{name}" VALUES(2,:second),(1,:first)'),
                {"first": b"\0\xff", "second": b"two"},
            )
    yield engine
    engine.dispose()


def test_master_proof_is_ordered_and_detects_changed_binary_value(masters):
    with masters.begin() as db:
        before = backup.master_proof(db, "gateway", checkpoint=lambda: None)
        db.execute(text("DELETE FROM users"))
        db.execute(
            text("INSERT INTO users VALUES(1,:first),(2,:second)"),
            {"first": b"\0\xff", "second": b"two"},
        )
        assert backup.master_proof(db, "gateway", checkpoint=lambda: None) == before
        db.execute(text("UPDATE users SET payload=:value WHERE id=1"), {"value": b"changed"})
        after = backup.master_proof(db, "gateway", checkpoint=lambda: None)
    assert before["tables"]["users"] != after["tables"]["users"]
    assert all(
        before["tables"][name] == after["tables"][name]
        for name in backup.MASTER_TABLES
        if name != "users"
    )


def test_proof_has_total_decoded_budget(masters, monkeypatch):
    monkeypatch.setattr(backup, "MAX_BYTES", 1)
    with masters.connect() as db, pytest.raises(ArchiveBlocked, match="budget"):
        backup.master_proof(db, "gateway", checkpoint=lambda: None)


def test_unknown_master_source_rejected(masters):
    with masters.connect() as db, pytest.raises(ArchiveBlocked, match="Unknown"):
        backup.master_proof(db, "other", checkpoint=lambda: None)


@pytest.mark.parametrize("version", [1, 4, True, 2.0, "2", None])
def test_master_scope_version_is_explicit_and_never_coerced(version):
    with pytest.raises(ArchiveBlocked, match="version"):
        backup.master_tables("gateway", proof_version=version)


@pytest.mark.parametrize("masters,proof_version", [(2, 2), (3, 3)], indirect=["masters"])
@pytest.mark.parametrize("changed_value", [False, True])
def test_versioned_restore_rechecks_real_sql_values_in_exact_scope(
    masters, tmp_path, monkeypatch, proof_version, changed_value
):
    """Exercise the restore coordinator; only PostgreSQL client/control SQL is adapted."""
    from types import SimpleNamespace

    from sqlalchemy import event

    from app.tiered_archive import database_restore

    with masters.connect() as db:
        original = backup.master_proof(
            db, "gateway", checkpoint=lambda: None, proof_version=proof_version
        )
        if proof_version == 2:
            with pytest.raises(ArchiveBlocked, match="Required master-data table missing"):
                backup.master_proof(db, "gateway", checkpoint=lambda: None)
    raw = masters.raw_connection()
    try:
        script = "\n".join(raw.driver_connection.iterdump())
    finally:
        raw.close()
    folder, sha, key = package(
        tmp_path, proof_version=proof_version, proof=original, dump_bytes=script.encode()
    )
    target = create_engine("sqlite:///" + str(tmp_path / "versioned-restored.sqlite"))

    def sqlite_control(conn, cursor, statement, parameters, context, executemany):
        if statement in {"SET TRANSACTION READ ONLY", "SET LOCAL statement_timeout='8000ms'"}:
            return "SELECT 1", ()
        return statement, parameters

    event.listen(target, "before_cursor_execute", sqlite_control, retval=True)
    clients = []

    def local_client(command, env, *, checkpoint, output=None):
        from pathlib import Path

        clients.append(command)
        if output is not None:
            # Decode only the decrypted archive input, never the source connection.
            output.write_bytes(Path(command[-1]).read_bytes())
        else:
            restored_script = next(Path(arg) for arg in command if arg.endswith("/masters.sql"))
            raw = target.raw_connection()
            try:
                raw.driver_connection.executescript(restored_script.read_text())
                if changed_value:
                    raw.driver_connection.execute("UPDATE users SET payload=X'bad0' WHERE id=1")
                    raw.commit()
            finally:
                raw.close()

    def isolated_engine(*args, **kwargs):
        assert args == (database_restore.restore_url(URL),)

        def connection():
            def options(**settings):
                assert settings == {"isolation_level": "REPEATABLE READ"}
                return target.connect().execution_options(isolation_level="SERIALIZABLE")

            return SimpleNamespace(execution_options=options)

        return SimpleNamespace(connect=connection, dispose=lambda: None)

    monkeypatch.setattr(database_restore, "run_client", local_client)
    monkeypatch.setattr(database_restore, "create_engine", isolated_engine)
    try:
        if changed_value:
            with pytest.raises(ArchiveBlocked, match="master-data proof differs"):
                restore_backup(folder, URL, sha, key, checkpoint=lambda: None)
        else:
            result = restore_backup(folder, URL, sha, key, checkpoint=lambda: None)
            assert result["master_proof_version"] == proof_version
            assert result["current_recovery_scope"] is (proof_version == 3)
            assert result["master_tables"] == (10 if proof_version == 2 else 13)
            assert result["typed_values_verified"] is True
            with target.connect() as db:
                assert (
                    backup.master_proof(
                        db, "gateway", checkpoint=lambda: None, proof_version=proof_version
                    )
                    == original
                )
        assert len(clients) == 4
        assert not list(folder.glob("restore-private-*"))
    finally:
        target.dispose()


@pytest.mark.parametrize("missing", backup.LEGACY_MASTER_TABLES_V2)
def test_version_two_restore_requires_every_original_master_table(tmp_path, missing):
    def remove(body, parts, folder):
        body["master_proof"]["tables"].pop(missing)

    folder, sha, key = package(tmp_path, remove, proof_version=2)
    with pytest.raises(ArchiveBlocked, match="Incomplete typed"):
        restore_backup(folder, URL, sha, key, checkpoint=lambda: None)


@pytest.mark.parametrize("index_version,body_version", [(2, 3), (3, 2), (2, True), (3, 2.0)])
def test_restore_body_and_index_proof_versions_must_match_exactly(
    tmp_path, index_version, body_version
):
    def mismatch(body, parts, folder):
        body["master_proof"]["version"] = body_version

    folder, sha, key = package(tmp_path, mismatch, proof_version=index_version)
    with pytest.raises(ArchiveBlocked, match="proof"):
        restore_backup(folder, URL, sha, key, checkpoint=lambda: None)


def test_review_only_command_does_not_read_profile_or_key(monkeypatch, capsys):
    from app.tiered_archive.__main__ import main

    monkeypatch.setattr(
        "sys.argv",
        [
            "archive",
            "restore-backup",
            "--folder",
            "/absent",
            "--profile-file",
            "/absent",
            "--key-file",
            "/absent",
            "--manifest-sha256",
            "a" * 64,
        ],
    )
    main()
    assert json.loads(capsys.readouterr().out) == {"status": "REVIEW_ONLY", "source_accesses": 0}


def test_client_runtime_requires_pinned_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("TIERED_ARCHIVE_PG_CLIENT_DIR", str(tmp_path))
    monkeypatch.delenv("TIERED_ARCHIVE_PG_CLIENT_MANIFEST_SHA256", raising=False)
    with pytest.raises(ArchiveBlocked, match="Pinned"):
        backup.client_command("pg_dump")


def test_changed_client_manifest_blocks_before_execution(tmp_path, monkeypatch):
    (tmp_path / "manifest.json").write_text("{}")
    monkeypatch.setenv("TIERED_ARCHIVE_PG_CLIENT_DIR", str(tmp_path))
    monkeypatch.setenv("TIERED_ARCHIVE_PG_CLIENT_MANIFEST_SHA256", "a" * 64)
    with pytest.raises(ArchiveBlocked, match="manifest differs"):
        backup.client_command("pg_restore")


def test_symlink_client_runtime_is_rejected(tmp_path, monkeypatch):
    original = tmp_path / "original"
    original.mkdir()
    link = tmp_path / "link"
    link.symlink_to(original)
    monkeypatch.setenv("TIERED_ARCHIVE_PG_CLIENT_DIR", str(link))
    monkeypatch.setenv("TIERED_ARCHIVE_PG_CLIENT_MANIFEST_SHA256", "a" * 64)
    with pytest.raises(ArchiveBlocked, match="Pinned"):
        backup.client_command("psql")


def test_no_arbitrary_executable_from_client_runtime():
    with pytest.raises(ArchiveBlocked, match="Unknown"):
        backup.client_command("sh")


@pytest.mark.parametrize(
    "name",
    [
        "alembic_version",
        "zone_access",
        "plant_sensor_assignment",
        "plant_observation_access",
        "form_submission",
        "access_invitation",
        "auth_session",
    ],
)
def test_missing_access_or_assignment_table_blocks_backup(masters, name):
    with masters.begin() as db:
        db.execute(text(f'DROP TABLE "{name}"'))
        with pytest.raises(ArchiveBlocked, match="Required master-data table missing: " + name):
            backup.master_proof(db, "gateway", checkpoint=lambda: None)


def test_permission_and_assignment_changes_invalidate_proof(masters):
    with masters.begin() as db:
        db.execute(text("DROP TABLE zone_access"))
        db.execute(
            text(
                "CREATE TABLE zone_access(user_id INTEGER, zone_id INTEGER, PRIMARY KEY(user_id,zone_id), FOREIGN KEY(user_id) REFERENCES users(id), FOREIGN KEY(zone_id) REFERENCES zone(id))"
            )
        )
        db.execute(text("INSERT INTO zone_access VALUES(1,1),(1,2)"))
        before = backup.master_proof(db, "gateway", checkpoint=lambda: None)
        assert before["version"] == backup.MASTER_PROOF_VERSION
        db.execute(text("DELETE FROM zone_access WHERE user_id=1 AND zone_id=2"))
        db.execute(
            text("UPDATE plant_sensor_assignment SET payload=:value WHERE id=1"),
            {"value": b"different sensor"},
        )
        after = backup.master_proof(db, "gateway", checkpoint=lambda: None)
    assert before["tables"]["zone_access"]["rows"] == 2
    assert after["tables"]["zone_access"]["rows"] == 1
    for name in ("zone_access", "plant_sensor_assignment"):
        assert before["tables"][name] != after["tables"][name]


def test_new_foreign_key_dependency_requires_explicit_backup_scope(masters):
    with masters.begin() as db:
        db.execute(text("CREATE TABLE external_owner(id INTEGER PRIMARY KEY)"))
        db.execute(text("DROP TABLE plant_sensor_assignment"))
        db.execute(
            text(
                "CREATE TABLE plant_sensor_assignment(id INTEGER PRIMARY KEY, owner INTEGER REFERENCES external_owner(id))"
            )
        )
        with pytest.raises(ArchiveBlocked, match="dependency outside backup scope"):
            backup.master_proof(db, "gateway", checkpoint=lambda: None)


def test_legacy_proof_cannot_claim_current_access_coverage(tmp_path):
    def legacy(body, parts, folder):
        body["master_proof"]["version"] = 1
        for name in ("zone_access", "plant_sensor_assignment", "plant_observation_access"):
            del body["master_proof"]["tables"][name]

    folder, sha, key = package(tmp_path, legacy)
    with pytest.raises(ArchiveBlocked, match="proof"):
        restore_backup(folder, URL, sha, key, checkpoint=lambda: None)


def test_access_tables_are_retained_and_never_expire_implicitly():
    from app.tiered_archive.source import DATASETS, RETAINED_TABLES

    authority = {"auth_session", "access_invitation", "form_submission"}
    assert {
        "zone_access",
        "plant_sensor_assignment",
        "plant_observation_access",
        *authority,
    } <= RETAINED_TABLES
    assert not authority.intersection(DATASETS)


@pytest.fixture
def authority_masters(tmp_path):
    """Actual auth/FK models in isolated SQLite; no native PostgreSQL claim."""
    from sqlalchemy import DateTime, Table, event
    from sqlalchemy.types import TypeDecorator

    from app.database import Base
    from app.models.access import AccessInvitation, AuthSession
    from app.models.form_submission import FormSubmission
    from app.models.user import Organization, User

    class UTCDateTime(TypeDecorator):
        impl = DateTime
        cache_ok = True

        def process_result_value(self, value, dialect):
            return (
                value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value
            )

    def utc_sqlite_columns(inspector, table, column):
        # SQLite strips timezone information; match PostgreSQL's UTC result type.
        if inspector.bind.dialect.name == "sqlite" and isinstance(column["type"], DateTime):
            column["type"] = UTCDateTime()

    event.listen(Table, "column_reflect", utc_sqlite_columns)
    engine = create_engine("sqlite:///" + str(tmp_path / "authority.sqlite"))
    models = [Organization, User, FormSubmission, AccessInvitation, AuthSession]
    Base.metadata.create_all(engine, tables=[model.__table__ for model in models])
    actual = {model.__tablename__ for model in models}
    organization, user, submission = (uuid.uuid4() for _ in range(3))
    with engine.begin() as db:
        db.execute(text("PRAGMA foreign_keys=ON"))
        for name in backup.MASTER_TABLES:
            if name not in actual:
                db.execute(text(f'CREATE TABLE "{name}"(id INTEGER PRIMARY KEY)'))
        db.execute(Organization.__table__.insert().values(id=organization, name="Synthetic"))
        db.execute(
            User.__table__.insert().values(
                id=user,
                email="synthetic@example.test",
                password_hash="synthetic",
                organization_id=organization,
            )
        )
        db.execute(
            FormSubmission.__table__.insert().values(
                id=submission,
                form_type="early_access",
                name="Synthetic",
                email="synthetic@example.test",
                message="Original request: ä\nsecond line",
            )
        )
        for index, state in enumerate(("active", "revoked", "expired")):
            db.execute(
                AuthSession.__table__.insert().values(
                    id=uuid.uuid4(),
                    user_id=user,
                    expires_at=AT + timedelta(days=-1 if state == "expired" else 1),
                    revoked_at=AT if state == "revoked" else None,
                )
            )
            db.execute(
                AccessInvitation.__table__.insert().values(
                    id=uuid.uuid4(),
                    submission_id=submission,
                    created_by=user,
                    email="synthetic@example.test",
                    token_hash=str(index) * 64,
                    expires_at=AT + timedelta(days=1),
                    delivery_status="sent",
                    revoked_at=AT if state == "revoked" else None,
                    redeemed_at=AT if state == "expired" else None,
                    redeemed_by=user if state == "expired" else None,
                )
            )
    try:
        yield engine
    finally:
        engine.dispose()
        event.remove(Table, "column_reflect", utc_sqlite_columns)


def test_authority_rows_and_revocations_survive_local_schema_restore(authority_masters, tmp_path):
    """Real SQL roundtrip and typed proof; pg_dump/Timescale need native acceptance."""
    with authority_masters.connect() as db:
        original = backup.master_proof(db, "gateway", checkpoint=lambda: None)
        assert not db.exec_driver_sql("PRAGMA foreign_key_check").all()
    assert original["version"] == 3
    assert original["tables"]["auth_session"]["rows"] == 3
    assert original["tables"]["access_invitation"]["rows"] == 3
    assert original["tables"]["form_submission"]["rows"] == 1
    raw = authority_masters.raw_connection()
    try:
        script = "\n".join(raw.driver_connection.iterdump())
    finally:
        raw.close()
    restored = create_engine("sqlite:///" + str(tmp_path / "authority-restored.sqlite"))
    try:
        raw = restored.raw_connection()
        try:
            raw.driver_connection.executescript(script)
        finally:
            raw.close()
        with restored.begin() as db:
            db.exec_driver_sql("PRAGMA foreign_keys=ON")
            assert not db.exec_driver_sql("PRAGMA foreign_key_check").all()
            assert backup.master_proof(db, "gateway", checkpoint=lambda: None) == original
            assert (
                db.execute(
                    text("SELECT count(*) FROM auth_session WHERE revoked_at IS NOT NULL")
                ).scalar_one()
                == 1
            )
            assert (
                db.execute(
                    text("SELECT count(*) FROM access_invitation WHERE revoked_at IS NOT NULL")
                ).scalar_one()
                == 1
            )
            assert (
                db.execute(
                    text("SELECT count(*) FROM access_invitation WHERE redeemed_by IS NOT NULL")
                ).scalar_one()
                == 1
            )
            db.execute(text("UPDATE auth_session SET revoked_at=NULL WHERE revoked_at IS NOT NULL"))
            changed = backup.master_proof(db, "gateway", checkpoint=lambda: None)
            assert changed["tables"]["auth_session"] != original["tables"]["auth_session"]
            db.execute(
                text("UPDATE access_invitation SET revoked_at=NULL WHERE revoked_at IS NOT NULL")
            )
            assert (
                backup.master_proof(db, "gateway", checkpoint=lambda: None)["tables"][
                    "access_invitation"
                ]
                != original["tables"]["access_invitation"]
            )
    finally:
        restored.dispose()


def test_version_two_proof_does_not_claim_current_session_authority(tmp_path):
    def legacy(body, parts, folder):
        body["master_proof"]["version"] = 2
        body["master_proof"]["tables"] = {
            name: value
            for name, value in body["master_proof"]["tables"].items()
            if name in backup.LEGACY_MASTER_TABLES_V2
        }

    folder, sha, key = package(tmp_path, legacy)
    with pytest.raises(ArchiveBlocked, match="proof"):
        restore_backup(folder, URL, sha, key, checkpoint=lambda: None)
    manifest = json.loads((folder / "manifest.json").read_text())
    # The original encrypted file format remains readable independently.
    backup.decrypt(
        folder / "0001.bin",
        tmp_path / "old-file-restored.dump",
        key,
        manifest["files"][1]["plaintext_sha256"],
        checkpoint=lambda: None,
    )
    assert (tmp_path / "old-file-restored.dump").read_bytes() == b"synthetic authenticated dump"


def systemd_key_acl(uid):
    import struct

    return struct.pack("<I", 2) + b"".join(
        struct.pack("<HHI", *entry)
        for entry in (
            (1, 4, 0xFFFFFFFF),
            (2, 4, uid),
            (4, 0, 0xFFFFFFFF),
            (16, 4, 0xFFFFFFFF),
            (32, 0, 0xFFFFFFFF),
        )
    )


def test_private_key_file_still_reads_exactly_32_bytes(tmp_path):
    key = tmp_path / "key"
    value = secrets.token_bytes(32)
    key.write_bytes(value)
    key.chmod(0o600)
    assert backup.key_from_file(key) == value
    key.chmod(0o644)
    with pytest.raises(ArchiveBlocked, match="private"):
        backup.key_from_file(key)


def test_systemd_credential_acl_is_private_to_root_and_current_service(monkeypatch):
    import os
    from types import SimpleNamespace

    info = SimpleNamespace(st_mode=0o100440, st_uid=0, st_gid=0)
    seen = []

    def read(fd, name):
        seen.append((fd, name))
        return systemd_key_acl(os.getuid())

    monkeypatch.setattr(os, "getxattr", read, raising=False)
    assert backup.private_key_permissions(123, info)
    assert seen == [(123, "system.posix_acl_access")]


@pytest.mark.parametrize(
    "case",
    [
        "other_user",
        "owning_group",
        "named_group",
        "world",
        "write",
        "malformed",
        "missing_acl",
        "nonroot_owner",
        "nonroot_group",
        "writable_mode",
    ],
)
def test_systemd_key_acl_never_allows_shared_or_writable_keys(monkeypatch, case):
    import os
    import struct
    from types import SimpleNamespace

    info = SimpleNamespace(st_mode=0o100440, st_uid=0, st_gid=0)
    acl = bytearray(systemd_key_acl(os.getuid()))
    if case == "other_user":
        acl = systemd_key_acl(os.getuid() + 1)
    elif case == "owning_group":
        struct.pack_into("<H", acl, 22, 4)
    elif case == "named_group":
        acl += struct.pack("<HHI", 8, 4, 123)
    elif case == "world":
        struct.pack_into("<H", acl, 38, 4)
    elif case == "write":
        struct.pack_into("<H", acl, 14, 6)
    elif case == "malformed":
        acl = acl[:-1]
    elif case == "nonroot_owner":
        info.st_uid = 123
    elif case == "nonroot_group":
        info.st_gid = 123
    elif case == "writable_mode":
        info.st_mode = 0o100460

    def read(*args):
        if case == "missing_acl":
            raise OSError("not supported")
        return bytes(acl)

    monkeypatch.setattr(os, "getxattr", read, raising=False)
    assert not backup.private_key_permissions(123, info)


def test_shared_key_mode_blocked_without_acl_api(monkeypatch):
    import os
    from types import SimpleNamespace

    monkeypatch.delattr(os, "getxattr", raising=False)
    info = SimpleNamespace(st_mode=0o100440, st_uid=0, st_gid=0)
    assert not backup.private_key_permissions(123, info)
