"""Interrupted recovery never accepts missing parts or adopts existing databases."""

import hashlib
import json
import sqlite3

import pytest

from app.raw_archive import catalog_resume as resume
from app.raw_archive import wav_catalog
from app.raw_archive.policy import ArchiveBlocked

from .test_wav_catalog import fixture_bundle


def setup(tmp_path):
    root, manifest = fixture_bundle(tmp_path)
    manifest["destination"] = "private-fixture"
    output = tmp_path / "new-recovery"
    output.mkdir(mode=0o700)
    destination = output / "archive.sqlite3"
    reference = {
        "key": "production/catalog-backup/fixture.json",
        "sha256": hashlib.sha256(wav_catalog.raw_json(manifest)).hexdigest(),
        "size": len(wav_catalog.raw_json(manifest)),
    }
    return root, manifest, destination, reference


def test_all_parts_resume_into_new_empty_database(tmp_path):
    root, manifest, destination, reference = setup(tmp_path)
    for index, entry in enumerate(manifest["files"]):
        assert (
            resume.restore_part(manifest, index, root / entry["file"], destination)["status"]
            == "restored_part"
        )
        assert (
            resume.restore_part(manifest, index, root / entry["file"], destination)["status"]
            == "reused"
        )
    proof = resume.finish(manifest, destination, reference)
    wav_catalog.validate_row("gateway", "organization", {"id": "company-id"})
    from app.raw_archive.readiness import validate_metadata_restore

    validate_metadata_restore(manifest, proof)
    assert proof["complete"] and proof["independent_read_identity"] is False
    assert proof["deletion_readiness"] is False and proof["deleted_files"] == 0
    with sqlite3.connect(destination) as db:
        assert db.execute("SELECT count(*) FROM records").fetchone()[0] == 2
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='users'").fetchall()


def test_missing_part_cannot_finish(tmp_path):
    root, manifest, destination, reference = setup(tmp_path)
    resume.restore_part(manifest, 0, root / manifest["files"][0]["file"], destination)
    with pytest.raises(ArchiveBlocked, match="Every"):
        resume.finish(manifest, destination, reference)


def test_out_of_order_parts_are_rejected(tmp_path):
    root, manifest, destination, _ = setup(tmp_path)
    with pytest.raises(ArchiveBlocked, match="order"):
        resume.restore_part(manifest, 1, root / manifest["files"][1]["file"], destination)
    with sqlite3.connect(destination) as db:
        assert not db.execute("SELECT * FROM restored_parts").fetchall()


def test_pause_rolls_back_rows_and_progress_together(tmp_path):
    root, manifest, destination, _ = setup(tmp_path)
    calls = 0

    def guard():
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise ArchiveBlocked("guard")

    with pytest.raises(ArchiveBlocked, match="guard"):
        resume.restore_part(manifest, 0, root / manifest["files"][0]["file"], destination, guard)
    with sqlite3.connect(destination) as db:
        assert db.execute("SELECT count(*) FROM archive").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM restored_parts").fetchone()[0] == 0
    assert (
        resume.restore_part(manifest, 0, root / manifest["files"][0]["file"], destination)["status"]
        == "restored_part"
    )


def test_existing_application_database_is_never_adopted(tmp_path):
    root, manifest, destination, _ = setup(tmp_path)
    with sqlite3.connect(destination) as db:
        db.execute("CREATE TABLE users(email TEXT)")
        db.execute("INSERT INTO users VALUES ('preserved')")
    destination.chmod(0o600)
    before = destination.read_bytes()
    with pytest.raises(ArchiveBlocked, match="not this"):
        resume.restore_part(manifest, 0, root / manifest["files"][0]["file"], destination)
    assert destination.read_bytes() == before


def test_changed_manifest_cannot_resume_old_database(tmp_path):
    root, manifest, destination, _ = setup(tmp_path)
    resume.restore_part(manifest, 0, root / manifest["files"][0]["file"], destination)
    changed = json.loads(json.dumps(manifest))
    changed["created_at"] = "2026-10-10T00:00:00+00:00"
    with pytest.raises(ArchiveBlocked, match="exact resumable"):
        resume.restore_part(changed, 1, root / manifest["files"][1]["file"], destination)


def test_tampered_part_cannot_advance_recovery(tmp_path):
    root, manifest, destination, _ = setup(tmp_path)
    path = root / manifest["files"][0]["file"]
    path.write_bytes(b"bad")
    with pytest.raises(ArchiveBlocked, match="readback"):
        resume.restore_part(manifest, 0, path, destination)
    assert not destination.exists()
