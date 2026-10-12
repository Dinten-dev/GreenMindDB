"""No partial publication or uploader-based recovery can prove completeness."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.raw_archive import catalog_stages, wav_catalog
from app.raw_archive import catalog_transfer as transfer
from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.read_only_box import CatalogReadOnlyBox

from .test_wav_catalog import fixture_bundle


class Box:
    identity = "private-fixture"

    def __init__(self):
        self.bodies = {}

    def capacity(self):
        return {"total_bytes": 1024**4, "available_bytes": 512 * 1024**3}

    def publish_catalog(self, path, key):
        self.bodies[key] = path.read_bytes()


class Reader(CatalogReadOnlyBox):
    identity = Box.identity

    def __init__(self, box):
        self.bodies = box.bodies
        self.downloads = 0

    def download_catalog(self, key, path, max_bytes, **kwargs):
        self.downloads += 1
        raw = self.bodies[key]
        assert len(raw) <= max_bytes
        path.write_bytes(raw)
        path.chmod(0o600)


def setup(tmp_path):
    root, manifest = fixture_bundle(tmp_path)
    wav_catalog.save_json(root / "manifest.json", manifest)
    return root, manifest, Box()


def publish_all(root, manifest, box):
    for i in range(len(manifest["files"])):
        transfer.publish_part(root / "manifest.json", i, box)
    return transfer.publish_manifest(root / "manifest.json", box)


def test_complete_independent_partwise_roundtrip(tmp_path):
    root, manifest, box = setup(tmp_path)
    ref = publish_all(root, manifest, box)
    reader = Reader(box)
    out = tmp_path / "new-empty-recovery"
    transfer.read_manifest(reader, ref, out)
    for i in range(len(manifest["files"])):
        assert transfer.restore_part(out, i, reader)["status"] == "restored_part"
    proof = transfer.finish(out, ref, reader)
    assert proof["tables"] == manifest["tables"]
    assert proof["independent_read_identity"] is True
    assert proof["deleted_files"] == 0 and proof["deletion_readiness"] is False
    assert reader.downloads == len(manifest["files"]) + 1


def test_missing_publication_never_exposes_manifest(tmp_path):
    root, _, box = setup(tmp_path)
    transfer.publish_part(root / "manifest.json", 0, box)
    with pytest.raises(ArchiveBlocked, match="Private recovery file"):
        transfer.publish_manifest(root / "manifest.json", box)
    assert all(key.endswith(".jsonl.gz") for key in box.bodies)


def test_upload_identity_cannot_certify_readback(tmp_path):
    root, manifest, box = setup(tmp_path)
    ref = publish_all(root, manifest, box)
    with pytest.raises(ArchiveBlocked, match="upload access forbidden"):
        transfer.read_manifest(box, ref, tmp_path / "restore")
    assert not (tmp_path / "restore").exists()


def test_remote_corruption_cannot_advance_database(tmp_path):
    root, manifest, box = setup(tmp_path)
    ref = publish_all(root, manifest, box)
    reader = Reader(box)
    out = tmp_path / "restore"
    transfer.read_manifest(reader, ref, out)
    remote = json.loads(box.bodies[ref["key"]])
    box.bodies[remote["files"][0]["remote_key"]] = b"bad"
    with pytest.raises(ArchiveBlocked, match="readback differs"):
        transfer.restore_part(out, 0, reader)
    assert not (out / "wav-catalog.sqlite3").exists()


def test_missing_restored_part_never_finishes(tmp_path):
    root, manifest, box = setup(tmp_path)
    ref = publish_all(root, manifest, box)
    reader = Reader(box)
    out = tmp_path / "restore"
    transfer.read_manifest(reader, ref, out)
    transfer.restore_part(out, 0, reader)
    with pytest.raises(ArchiveBlocked, match="Private recovery file"):
        transfer.finish(out, ref, reader)
    assert not (out / "verified.json").exists()


def test_changed_publication_manifest_cannot_reuse_proof(tmp_path):
    root, manifest, box = setup(tmp_path)
    transfer.publish_part(root / "manifest.json", 0, box)
    manifest["created_at"] = "2026-10-01T00:00:00+00:00"
    (root / "manifest.json").write_bytes(wav_catalog.raw_json(manifest))
    with pytest.raises(ArchiveBlocked, match="proof changed"):
        transfer.publish_part(root / "manifest.json", 0, box)


def test_unknown_quota_refuses_upload(tmp_path):
    root, _, box = setup(tmp_path)
    box.capacity = lambda: {}
    with pytest.raises(KeyError):
        transfer.publish_part(root / "manifest.json", 0, box)
    assert not box.bodies


def stages_fixture(tmp_path):
    root, manifest, _ = setup(tmp_path)
    stages = {}
    now = datetime.now(UTC).isoformat()
    for kind in catalog_stages.KINDS:
        folder = tmp_path / kind
        folder.mkdir(mode=0o700)
        entries = [e for e in manifest["files"] if e["kind"] == kind]
        for entry in entries:
            (folder / entry["file"]).write_bytes((root / entry["file"]).read_bytes())
            (folder / entry["file"]).chmod(0o600)
        extra = {"finished_at": now}
        if kind == "ledger":
            extra.update(processed_at=now, source_sha256="0" * 64)
        if kind == "direct":
            extra["direct_bucket"] = "greenmind-direct-production-hotspot"
        proof = catalog_stages.completed_stage(
            kind, now, entries, manifest["tables"].get(kind, {}), **extra
        )
        wav_catalog.save_json(folder / "stage.json", proof)
        stages[kind] = folder
    return stages


def test_preparation_retains_originals_and_never_claims_recovery(tmp_path):
    stages = stages_fixture(tmp_path)
    before = {str(f): f.read_bytes() for p in stages.values() for f in p.iterdir()}
    out = tmp_path / "prepared"
    result = transfer.prepare(stages, out)
    assert result["complete_recovery"] is False
    assert result["deleted_files"] == 0
    assert before == {str(f): f.read_bytes() for p in stages.values() for f in p.iterdir()}
    assert not (out / "verified.json").exists()


@pytest.mark.parametrize("change", ["stale", "wrong_bucket"])
def test_stale_or_cross_environment_stage_cannot_prepare(tmp_path, change):
    stages = stages_fixture(tmp_path)
    path = stages["direct"] / "stage.json"
    proof = json.loads(path.read_text())
    if change == "stale":
        old = (datetime.now(UTC) - timedelta(days=2)).isoformat()
        proof.update(started_at=old, finished_at=old)
    else:
        proof["direct_bucket"] = "greenmind-direct-staging-hotspot"
    path.write_bytes(wav_catalog.raw_json(proof))
    with pytest.raises(ArchiveBlocked):
        transfer.prepare(stages, tmp_path / "prepared")
    assert not (tmp_path / "prepared").exists()
