import json
import sqlite3

import pytest

from app.raw_archive.export_checkpoint import digest, merge_into_new, restore, verify
from app.raw_archive.policy import ArchiveBlocked


def checkpoint(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    with sqlite3.connect(source / "jobs.sqlite3") as db:
        db.execute(
            "CREATE TABLE jobs(id TEXT,user_id TEXT,parts TEXT,status TEXT,completed INTEGER)"
        )
        db.execute(
            "INSERT INTO jobs VALUES(?,?,?,?,?)", ("job", "owner", '["job.zip"]', "ready", 1)
        )
    (source / "job.zip").write_bytes(b"original export bytes")
    files = {p.name: {"bytes": p.stat().st_size, "sha256": digest(p)} for p in source.iterdir()}
    manifest = source / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": 1,
                "complete": True,
                "originals_retained": True,
                "deleted_files": 0,
                "jobs": 1,
                "files": files,
            }
        )
    )
    return source, digest(manifest)


def test_restore_preserves_source_bytes_and_job_ownership(tmp_path):
    source, sha = checkpoint(tmp_path)
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    result = restore(source, tmp_path / "new", sha)
    assert result["jobs"] == 1 and result["ownership_accepted"] is False
    assert {p.name: p.read_bytes() for p in source.iterdir()} == before
    with sqlite3.connect(tmp_path / "new/jobs.sqlite3") as db:
        assert db.execute("SELECT user_id,status,completed FROM jobs").fetchone() == (
            "owner",
            "ready",
            1,
        )


def test_corrupt_part_rejected_before_destination_creation(tmp_path):
    source, sha = checkpoint(tmp_path)
    (source / "job.zip").write_bytes(b"changed")
    with pytest.raises(ArchiveBlocked, match="bytes differ"):
        restore(source, tmp_path / "new", sha)
    assert not (tmp_path / "new").exists()


def test_existing_store_and_unknown_file_never_overwritten(tmp_path):
    source, sha = checkpoint(tmp_path)
    output = tmp_path / "existing"
    output.mkdir()
    (output / "proof").write_text("preserve")
    with pytest.raises(ArchiveBlocked, match="empty destination"):
        restore(source, output, sha)
    assert (output / "proof").read_text() == "preserve"
    (source / "unlisted.raw-part").write_bytes(b"unknown")
    with pytest.raises(ArchiveBlocked, match="inventory"):
        verify(source, sha)


def test_valid_hash_but_missing_referenced_zip_rejected(tmp_path):
    source, sha = checkpoint(tmp_path)
    with sqlite3.connect(source / "jobs.sqlite3") as db:
        db.execute("UPDATE jobs SET parts=?", ('["missing.zip"]',))
    p = source / "manifest.json"
    v = json.loads(p.read_text())
    v["files"]["jobs.sqlite3"] = {
        "bytes": (source / "jobs.sqlite3").stat().st_size,
        "sha256": digest(source / "jobs.sqlite3"),
    }
    p.write_text(json.dumps(v))
    with pytest.raises(ArchiveBlocked, match="ZIP missing"):
        verify(source, digest(p))


def test_merge_keeps_new_private_jobs_and_every_original_file(tmp_path):
    source, sha = checkpoint(tmp_path)
    existing = tmp_path / "existing"
    existing.mkdir()
    with sqlite3.connect(existing / "jobs.sqlite3") as db:
        db.execute(
            "CREATE TABLE jobs(id TEXT,user_id TEXT,parts TEXT,status TEXT,completed INTEGER)"
        )
        db.execute(
            "INSERT INTO jobs VALUES(?,?,?,?,?)",
            ("private-new", "second-owner", '["private-new.zip"]', "ready", 1),
        )
    (existing / "private-new.zip").write_bytes(b"new verified part")
    before = {p.name: p.read_bytes() for p in existing.iterdir()}
    result = merge_into_new(source, existing, tmp_path / "combined", sha)
    assert result["combined_jobs"] == 2 and result["existing_jobs_preserved"] == 1
    assert {p.name: p.read_bytes() for p in existing.iterdir()} == before
    assert (tmp_path / "combined/private-new.zip").read_bytes() == b"new verified part"


def test_conflicting_job_id_cannot_overwrite_either_original(tmp_path):
    source, sha = checkpoint(tmp_path)
    existing = tmp_path / "existing"
    existing.mkdir()
    with sqlite3.connect(existing / "jobs.sqlite3") as db:
        db.execute(
            "CREATE TABLE jobs(id TEXT,user_id TEXT,parts TEXT,status TEXT,completed INTEGER)"
        )
        db.execute(
            "INSERT INTO jobs VALUES(?,?,?,?,?)", ("job", "different-owner", "[]", "queued", 0)
        )
    before = (existing / "jobs.sqlite3").read_bytes()
    with pytest.raises(ArchiveBlocked, match="Conflicting job"):
        merge_into_new(source, existing, tmp_path / "combined", sha)
    assert (existing / "jobs.sqlite3").read_bytes() == before


def test_combined_store_cannot_exceed_byte_budget(tmp_path, monkeypatch):
    import app.raw_archive.export_checkpoint as module

    source, sha = checkpoint(tmp_path)
    (source / "job.zip").write_bytes(b"a" * 6000)
    manifest = source / "manifest.json"
    proof = json.loads(manifest.read_text())
    proof["files"]["job.zip"] = {"bytes": 6000, "sha256": digest(source / "job.zip")}
    manifest.write_text(json.dumps(proof))
    sha = digest(manifest)
    existing = tmp_path / "existing"
    existing.mkdir()
    with sqlite3.connect(existing / "jobs.sqlite3") as db:
        db.execute(
            "CREATE TABLE jobs(id TEXT,user_id TEXT,parts TEXT,status TEXT,completed INTEGER)"
        )
        db.execute(
            "INSERT INTO jobs VALUES(?,?,?,?,?)", ("new", "owner", '["new.zip"]', "ready", 1)
        )
    (existing / "new.zip").write_bytes(b"b" * 6000)
    monkeypatch.setattr(module, "MAX_BYTES", 20000)
    with pytest.raises(ArchiveBlocked, match="Combined private store exceeds byte"):
        merge_into_new(source, existing, tmp_path / "combined", sha)
    assert (existing / "new.zip").read_bytes() == b"b" * 6000
