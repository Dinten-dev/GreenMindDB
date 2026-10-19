"""Resume exact counts without changing independently restored catalog bytes."""

import json

import pytest

from app.raw_archive import catalog_reconcile_resume as scan
from app.raw_archive import catalog_resume
from app.raw_archive.policy import ArchiveBlocked, checksum
from app.raw_archive.wav_catalog import raw_json, save_json

from .test_wav_reconcile import inventory


def recovered(tmp_path):
    manifest_path, record = inventory(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    manifest["destination"] = "private-fixture"
    folder = tmp_path / "recovery"
    folder.mkdir(mode=0o700)
    save_json(folder / "manifest.json", manifest)
    reference = {
        "key": "production/catalog-backup/fixture.json",
        "sha256": checksum(folder / "manifest.json"),
        "size": len(raw_json(manifest)),
    }
    db = folder / "wav-catalog.sqlite3"
    for i, entry in enumerate(manifest["files"]):
        catalog_resume.restore_part(manifest, i, manifest_path.parent / entry["file"], db)
    proof = catalog_resume.finish(manifest, db, reference)
    proof["independent_read_identity"] = True
    save_json(folder / "verified.json", proof)
    return folder, record


def test_complete_reconciliation_preserves_accepted_recovery(tmp_path):
    recovery, record = recovered(tmp_path)
    db = recovery / "wav-catalog.sqlite3"
    original = checksum(db)
    output = tmp_path / "scan"
    scan.prepare(recovery, output)
    for name in scan.PHASES:
        assert scan.phase(output, name, limit=1)["status"] == "phase_complete"
        assert scan.phase(output, name, limit=1)["status"] == "reused"
    proof = scan.finish(output)
    assert proof["complete"] and proof["counts"]["gateway"]["matched"] == 1
    assert checksum(db) == original and proof["deleted_files"] == 0
    assert proof["deletion_ready"] is False


def test_capacity_pause_rolls_back_cursor_and_counts(tmp_path):
    recovery, _ = recovered(tmp_path)
    output = tmp_path / "scan"
    scan.prepare(recovery, output)
    scan.phase(output, "segments")
    calls = 0

    def pause():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise ArchiveBlocked("pause")

    with pytest.raises(ArchiveBlocked, match="pause"):
        scan.phase(output, "gateway", checkpoint=pause)
    scan.phase(output, "gateway")
    scan.phase(output, "direct")
    assert scan.finish(output)["counts"]["gateway"]["files"] == 1


def test_quarantine_cannot_become_eligible(tmp_path):
    recovery, record = recovered(tmp_path)
    output = tmp_path / "scan"
    scan.prepare(recovery, output, excluded=[("gateway", record["identity"])])
    for name in scan.PHASES:
        scan.phase(output, name)
    proof = scan.finish(output)
    assert proof["counts"]["gateway"]["excluded"] == 1
    assert proof["counts"]["gateway"]["matched"] == 0
    assert (
        json.loads((output / "quarantine.json").read_text())["excluded"][0]["identity"]
        == record["identity"]
    )


def test_recovery_change_rejected_before_progress(tmp_path):
    recovery, _ = recovered(tmp_path)
    output = tmp_path / "scan"
    scan.prepare(recovery, output)
    with (recovery / "wav-catalog.sqlite3").open("ab") as f:
        f.write(b"changed")
    with pytest.raises(ArchiveBlocked, match="changed"):
        scan.phase(output, "segments")


def test_changed_acceptance_proof_rejected_at_finish(tmp_path):
    recovery, _ = recovered(tmp_path)
    output = tmp_path / "scan"
    scan.prepare(recovery, output)
    for name in scan.PHASES:
        scan.phase(output, name)
    proof = json.loads((recovery / "verified.json").read_text())
    proof["complete"] = False
    (recovery / "verified.json").write_bytes(raw_json(proof))
    with pytest.raises(ArchiveBlocked, match="changed"):
        scan.finish(output)
    assert not (output / "reconciliation.json").exists()
