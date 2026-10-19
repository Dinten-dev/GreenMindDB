import json
import sqlite3

import pytest

from app.raw_archive import catalog_integrity_resume as integrity
from app.raw_archive import catalog_resume as resume
from app.raw_archive.policy import ArchiveBlocked

from .test_catalog_resume import setup


def recovered(tmp_path):
    root, manifest, database, reference = setup(tmp_path)
    for i, e in enumerate(manifest["files"]):
        resume.restore_part(manifest, i, root / e["file"], database)
    destination = database.with_name("wav-catalog.sqlite3")
    database.rename(destination)
    database = destination
    path = database.parent / "manifest.json"
    path.write_text(json.dumps(manifest))
    path.chmod(0o600)
    return database, reference


def complete(tmp_path):
    database, reference = recovered(tmp_path)
    out = tmp_path / "integrity"
    integrity.prepare(database.parent, out)
    for phase in ("quick", *integrity.INDEXES):
        while integrity.phase(out, phase, limit=1)["status"] == "phase_progress":
            pass
    proof = integrity.finish(out)
    return database, out, proof


def test_every_primary_index_is_verified_and_source_unchanged(tmp_path):
    database, reference = recovered(tmp_path)
    before = database.read_bytes()
    out = tmp_path / "integrity"
    integrity.prepare(database.parent, out)
    with pytest.raises(ArchiveBlocked, match="structure check"):
        integrity.phase(out, "records")
    for phase in ("quick", *integrity.INDEXES):
        while integrity.phase(out, phase, limit=1)["status"] == "phase_progress":
            pass
    proof = integrity.finish(out)
    assert database.read_bytes() == before and proof["complete"]
    assert proof["quick_check_alone_accepted"] is False
    assert proof["all_index_rows_verified"]["records"] == 2
    integrity.validate(out / "verified.json", database, database.parent / "manifest.json")


def test_quick_check_alone_cannot_be_accepted(tmp_path):
    database, _ = recovered(tmp_path)
    out = tmp_path / "integrity"
    integrity.prepare(database.parent, out)
    integrity.phase(out, "quick")
    with pytest.raises(ArchiveBlocked, match="All complete"):
        integrity.finish(out)
    assert not (out / "verified.json").exists()


def test_changed_database_invalidates_prior_progress(tmp_path):
    database, _ = recovered(tmp_path)
    out = tmp_path / "integrity"
    integrity.prepare(database.parent, out)
    with sqlite3.connect(database) as db:
        db.execute("UPDATE table_counts SET n=n+1")
    with pytest.raises(ArchiveBlocked, match="Fixed recovery changed"):
        integrity.phase(out, "quick")


def test_pause_never_commits_partial_index_progress(tmp_path):
    database, _ = recovered(tmp_path)
    out = tmp_path / "integrity"
    integrity.prepare(database.parent, out)
    integrity.phase(out, "quick")
    calls = 0

    def guard():
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise ArchiveBlocked("pause")

    with pytest.raises(ArchiveBlocked, match="pause"):
        integrity.phase(out, "records", guard)
    with sqlite3.connect(out / "progress.sqlite3") as db:
        assert db.execute(
            "SELECT cursor,checked,done FROM progress WHERE name='records'"
        ).fetchone() == (0, 0, 0)


def test_unknown_secondary_index_is_rejected(tmp_path):
    database, _ = recovered(tmp_path)
    with sqlite3.connect(database) as db:
        db.execute("CREATE INDEX extra ON records(ordinal)")
    with pytest.raises(ArchiveBlocked, match="Exact unique"):
        integrity.prepare(database.parent, tmp_path / "integrity")


def test_proof_rejects_missing_index_coverage(tmp_path):
    database, out, proof = complete(tmp_path)
    proof["all_index_rows_verified"].pop("records")
    (out / "verified.json").write_text(json.dumps(proof))
    with pytest.raises(ArchiveBlocked, match="Exact complete"):
        integrity.validate(out / "verified.json", database, database.parent / "manifest.json")


def test_structurally_corrupt_database_never_gets_verified(tmp_path):
    database, _ = recovered(tmp_path)
    raw = bytearray(database.read_bytes())
    raw[0:16] = b"broken-db-header"
    database.write_bytes(raw)
    with pytest.raises((sqlite3.DatabaseError, ArchiveBlocked)):
        integrity.prepare(database.parent, tmp_path / "integrity")
