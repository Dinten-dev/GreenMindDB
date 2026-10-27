"""Authenticated backup bindings and typed master-data proofs without live services."""

import json
import secrets
from datetime import timedelta

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


def package(tmp_path, change=None):
    folder = tmp_path / "bundle"
    folder.mkdir()
    key = secrets.token_bytes(32)
    parts = []
    for index in (1, 2):
        plain = tmp_path / f"{index}.dump"
        plain.write_bytes(b"synthetic authenticated dump")
        parts.append(
            backup.encrypt(plain, folder / f"{index:04}.bin", key, checkpoint=lambda: None)
        )
    body = {
        "source": "gateway",
        "at": AT,
        "files": [dict(part) for part in parts],
        "master_proof": {
            "version": backup.MASTER_PROOF_VERSION,
            "tables": {
                table: {"rows": 0, "content_sha256": "a" * 64} for table in backup.MASTER_TABLES
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
        master_proof_version=backup.MASTER_PROOF_VERSION,
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


def test_wrong_key_never_opens_database_and_cleans_plaintext(tmp_path):
    folder, sha, _ = package(tmp_path)
    with pytest.raises(ArchiveBlocked, match="authentication"):
        restore_backup(folder, URL, sha, secrets.token_bytes(32), checkpoint=lambda: None)
    assert not list(folder.glob("restore-private-*"))


def test_second_authenticated_record_failure_before_database(tmp_path):
    def tamper(body, parts, folder):
        path = folder / "0002.bin"
        raw = bytearray(path.read_bytes())
        raw[-1] ^= 1
        path.write_bytes(raw)
        parts[1]["sha256"] = checksum(path)
        body["files"][1] = dict(parts[1])

    folder, sha, key = package(tmp_path, tamper)
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
def masters(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "masters.sqlite"))
    with engine.begin() as db:
        for name in backup.MASTER_TABLES:
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
    ["alembic_version", "zone_access", "plant_sensor_assignment", "plant_observation_access"],
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
    from app.tiered_archive.source import RETAINED_TABLES

    assert {"zone_access", "plant_sensor_assignment", "plant_observation_access"} <= RETAINED_TABLES


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
