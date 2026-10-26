import importlib.util
import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.raw_archive import ledger_checkpoint_stage as stage
from app.raw_archive.policy import ArchiveBlocked, checksum

from .test_wav_catalog import fixture_bundle

MODULE = Path(__file__).resolve().parents[3] / "deploy/archive-server/source-journal-checkpoint.py"


def module():
    spec = importlib.util.spec_from_file_location("journal_checkpoint", MODULE)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_real_shared_lock_pauses_and_requests_yield(tmp_path, monkeypatch):
    import fcntl

    m = module()
    monkeypatch.setattr(m, "COORD", tmp_path)
    with (tmp_path / "jobs.lock").open("w") as locked:
        os.fchmod(locked.fileno(), 0o660)
        fcntl.flock(locked, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(m.Pause, match="archive_job_busy"):
            with m.shared_lease():
                pytest.fail("Cannot acquire a second writer lease")
    assert json.loads((tmp_path / "catalog.request").read_text())["expires"] > m.time.time()


def test_pause_before_source_open_preserves_all_files(tmp_path, monkeypatch):
    m = module()
    monkeypatch.setattr(m, "COORD", tmp_path)

    def stop():
        raise m.Pause("source_memory")

    with pytest.raises(m.Pause, match="source_memory"):
        m.checkpoint(tmp_path / "new.sqlite3", healthy=stop)
    assert not (tmp_path / "new.sqlite3").exists()
    assert not (tmp_path / "journal-proof.json").exists()


def test_online_backup_includes_wal_without_changing_source(tmp_path, monkeypatch):
    m = module()
    coord = tmp_path / "coord"
    coord.mkdir(mode=0o700)
    monkeypatch.setattr(m, "COORD", coord)
    source = tmp_path / "source.sqlite3"
    monkeypatch.setattr(m, "SOURCE", source)
    with sqlite3.connect(source) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE archive(id TEXT)")
        db.execute("INSERT INTO archive VALUES ('committed-wal')")
        db.commit()
        hashes = {p: checksum(p) for p in (source, source.with_name(source.name + "-wal"))}
        proof = m.checkpoint(tmp_path / "new.sqlite3", healthy=lambda: None)
        assert proof["rows"] == 1 and proof["complete"] and not proof["compression_on_source"]
        assert all(checksum(p) == sha for p, sha in hashes.items())
        with sqlite3.connect(tmp_path / "new.sqlite3") as restored:
            assert restored.execute("SELECT id FROM archive").fetchone()[0] == "committed-wal"


def test_wal_profile_keeps_original_database_and_wal_readonly(tmp_path):
    m = module()
    paths = m.sqlite_paths(tmp_path)
    assert paths["ReadOnlyPaths"].split() == [str(m.SOURCE), "-" + str(m.SOURCE) + "-wal"]
    assert paths["ReadWritePaths"].split() == [str(tmp_path), str(m.COORD), str(m.SOURCE.parent)]


def test_backup_without_existing_wal_retains_original_bytes(tmp_path, monkeypatch):
    m = module()
    coord = tmp_path / "coord"
    coord.mkdir(mode=0o700)
    monkeypatch.setattr(m, "COORD", coord)
    source = tmp_path / "source.sqlite3"
    monkeypatch.setattr(m, "SOURCE", source)
    db = sqlite3.connect(source)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE archive(id TEXT)")
    db.execute("INSERT INTO archive VALUES ('closed-writer')")
    db.commit()
    db.close()
    assert not source.with_name(source.name + "-wal").exists()
    before = checksum(source)
    proof = m.checkpoint(tmp_path / "new.sqlite3", healthy=lambda: None)
    assert proof["rows"] == 1 and checksum(source) == before


@pytest.mark.parametrize(
    "corruption", ["sha256", "rows", "created_by_uid", "stale", "archive_integrity_required"]
)
def test_conversion_rejects_invalid_provenance(tmp_path, corruption):
    fixture_bundle(tmp_path)
    source = tmp_path / "source/archive.sqlite3"
    os.chmod(source, 0o600)
    now = datetime.now(UTC)
    proof = {
        "schema": 1,
        "complete": True,
        "source_integrity_checked": False,
        "archive_integrity_required": True,
        "source_authority": "Biolingo",
        "environment": "production",
        "created_by_uid": 996,
        "started_at": (now - timedelta(seconds=1)).isoformat(),
        "finished_at": now.isoformat(),
        "rows": 1,
        "bytes": source.stat().st_size,
        "sha256": checksum(source),
    }
    if corruption == "stale":
        proof["started_at"] = proof["finished_at"] = (now - timedelta(days=2)).isoformat()
    else:
        proof[corruption] = -1 if corruption != "sha256" else "a" * 64
    path = tmp_path / "proof.json"
    path.write_text(json.dumps(proof))
    path.chmod(0o600)
    with pytest.raises(ArchiveBlocked):
        stage.convert(source, path, tmp_path / "new")
    assert not (tmp_path / "new/stage.json").exists()


def test_valid_checkpoint_converts_without_source_change(tmp_path):
    fixture_bundle(tmp_path)
    source = tmp_path / "source/archive.sqlite3"
    source.chmod(0o600)
    before = checksum(source)
    now = datetime.now(UTC)
    proof = {
        "schema": 1,
        "complete": True,
        "source_integrity_checked": False,
        "archive_integrity_required": True,
        "source_authority": "Biolingo",
        "environment": "production",
        "created_by_uid": 996,
        "started_at": (now - timedelta(seconds=1)).isoformat(),
        "finished_at": now.isoformat(),
        "rows": 1,
        "bytes": source.stat().st_size,
        "sha256": before,
    }
    path = tmp_path / "proof.json"
    path.write_text(json.dumps(proof))
    path.chmod(0o600)
    value = stage.convert(source, path, tmp_path / "new")
    assert value["status"] == "ledger_complete" and checksum(source) == before
    assert (tmp_path / "new/stage.json").exists()
    assert json.loads((tmp_path / "new/stage.json").read_text())["archive_integrity_checked"]


def test_valid_hash_does_not_accept_corrupt_checkpoint(tmp_path):
    source = tmp_path / "invalid.sqlite3"
    source.write_bytes(b"invalid SQLite pages")
    source.chmod(0o600)
    now = datetime.now(UTC).isoformat()
    proof = {
        "schema": 1,
        "complete": True,
        "source_integrity_checked": False,
        "archive_integrity_required": True,
        "source_authority": "Biolingo",
        "environment": "production",
        "created_by_uid": 996,
        "started_at": now,
        "finished_at": now,
        "rows": 1,
        "bytes": source.stat().st_size,
        "sha256": checksum(source),
    }
    path = tmp_path / "proof.json"
    path.write_text(json.dumps(proof))
    path.chmod(0o600)
    with pytest.raises(sqlite3.DatabaseError):
        stage.convert(source, path, tmp_path / "new")
    assert not (tmp_path / "new").exists()


def test_sql_progress_pause_preserves_reason_and_original(tmp_path, monkeypatch):
    m = module()
    coord = tmp_path / "coord"
    coord.mkdir(mode=0o700)
    monkeypatch.setattr(m, "COORD", coord)
    source = tmp_path / "source.sqlite3"
    monkeypatch.setattr(m, "SOURCE", source)
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE archive(id TEXT)")
        db.executemany("INSERT INTO archive VALUES (?)", ((str(i),) for i in range(2000)))
    before = checksum(source)
    inside_sql = False
    real_connect = sqlite3.connect

    class Connection(sqlite3.Connection):
        def set_progress_handler(self, callback, instructions):
            nonlocal inside_sql
            inside_sql = True
            return super().set_progress_handler(callback, instructions)

    def connect(*args, **kwargs):
        return real_connect(*args, **kwargs, factory=Connection)

    ticks = iter(range(100))
    monkeypatch.setattr(m.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(m.sqlite3, "connect", connect)

    def healthy():
        if inside_sql:
            raise m.Pause("source_memory")

    with pytest.raises(m.Pause, match="source_memory"):
        m.checkpoint(tmp_path / "partial.sqlite3", healthy=healthy)
    assert checksum(source) == before
    assert not (tmp_path / "journal-proof.json").exists()
