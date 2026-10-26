"""A partial or corrupt catalog must never become independent recovery evidence."""

import json
import os
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.raw_archive import archive_catalog_finalize as finalizer
from app.raw_archive import catalog_stages, source_ledger_once, wav_catalog
from app.raw_archive.health import SafetyPause
from app.raw_archive.policy import ArchiveBlocked

from .test_archive_host_catalog import Box, Probe
from .test_wav_catalog import fixture_bundle


@pytest.fixture
def source_config(monkeypatch):
    for key, value in {
        "RAW_ARCHIVE_ENVIRONMENT": "production",
        "RAW_ARCHIVE_COORDINATION_DIR": "/var/lib/greenmind-archive-coordination",
        **{
            name: "false"
            for name in (
                "RAW_ARCHIVE_ENABLED",
                "RAW_ARCHIVE_DELETE_ENABLED",
                "RAW_ARCHIVE_LEGACY_NULL_ACCEPTED",
                "RETENTION_ENABLED",
                "DIRECT_RETENTION_ENABLED",
                "VISUAL_PRUNE_ENABLED",
            )
        },
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("RAW_ARCHIVE_WORKER_ROLE", raising=False)
    monkeypatch.setattr(
        source_ledger_once.os, "uname", lambda: SimpleNamespace(nodename="Biolingo")
    )
    monkeypatch.setattr(
        source_ledger_once.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=os.geteuid())
    )
    monkeypatch.setattr(source_ledger_once, "lease", lambda _: nullcontext())


def test_source_pause_does_not_touch_live_journal(tmp_path, monkeypatch, source_config):
    probe = Probe(False)
    monkeypatch.setattr(source_ledger_once, "health_probe", lambda: probe)
    monkeypatch.setattr(
        source_ledger_once, "backup_ledger", lambda *a, **kw: pytest.fail("No source read")
    )
    with pytest.raises(SafetyPause):
        source_ledger_once.run(tmp_path / "new")
    assert not (tmp_path / "new").exists() and probe.closed


def test_source_checkpoint_uses_online_backup_and_preserves_original(
    tmp_path, monkeypatch, source_config
):
    fixture_bundle(tmp_path)
    source = tmp_path / "source/archive.sqlite3"
    before = wav_catalog.checksum(source)
    monkeypatch.setattr(source_ledger_once, "SOURCE", source)
    monkeypatch.setattr(source_ledger_once, "health_probe", lambda: Probe(True))
    output = tmp_path / "new"
    result = source_ledger_once.run(output)
    assert result["status"] == "ledger_complete" and result["complete_catalog"] is False
    assert result["rows"] == 1 and wav_catalog.checksum(source) == before
    stage = catalog_stages.load_stage(output, "ledger")
    assert sum(part["rows"] for part in stage["files"]) == 1
    assert stage["source_sha256"] == wav_catalog.checksum(output / "source-ledger.sqlite3")


def prepared(tmp_path, monkeypatch):
    root, manifest = fixture_bundle(tmp_path)
    now = datetime.now(UTC)
    stages = {}
    for kind in catalog_stages.KINDS:
        folder = tmp_path / kind
        folder.mkdir(mode=0o700)
        parts = [part for part in manifest["files"] if part["kind"] == kind]
        for part in parts:
            os.link(root / part["file"], folder / part["file"])
        extra = (
            {"source_sha256": "a" * 64, "processed_at": now.isoformat()}
            if kind == "ledger"
            else {
                "direct_bucket": "greenmind-direct-production-hotspot",
            }
        )
        stage = catalog_stages.completed_stage(
            kind,
            (now - timedelta(seconds=10)).isoformat(),
            parts,
            manifest["tables"].get(kind),
            finished_at=now.isoformat(),
            **extra,
        )
        wav_catalog.save_json(folder / "stage.json", stage)
        stages[kind] = folder
    quarantine = tmp_path / "quarantine.json"
    wav_catalog.save_json(quarantine, {"schema": 1, "environment": "production", "excluded": []})
    monkeypatch.setattr(finalizer.archive_host_catalog, "configured", lambda: None)
    monkeypatch.setattr(finalizer.archive_host_catalog, "health_probe", lambda: Probe(True))
    return stages, quarantine


def test_missing_stage_blocks_before_publication(tmp_path, monkeypatch):
    stages, quarantine = prepared(tmp_path, monkeypatch)
    monkeypatch.setattr(
        finalizer.archive_host_catalog, "publish_restore", lambda *a: pytest.fail("No publication")
    )
    stages.pop("ledger")
    with pytest.raises(ArchiveBlocked, match="three stages"):
        finalizer.finalize(stages, quarantine, tmp_path / "complete")
    assert not (tmp_path / "complete").exists()


def test_stale_source_blocks_before_any_output(tmp_path, monkeypatch):
    stages, quarantine = prepared(tmp_path, monkeypatch)
    with pytest.raises(ArchiveBlocked, match="fresh"):
        finalizer.finalize(
            stages, quarantine, tmp_path / "complete", now=datetime.now(UTC) + timedelta(days=2)
        )
    assert not (tmp_path / "complete").exists()


def test_corrupt_stage_retains_original_and_blocks_publication(tmp_path, monkeypatch):
    stages, quarantine = prepared(tmp_path, monkeypatch)
    part = next(stages["gateway"].glob("*.gz"))
    part.write_bytes(b"corrupt")
    with pytest.raises(ArchiveBlocked, match="part changed"):
        finalizer.finalize(stages, quarantine, tmp_path / "complete")
    assert part.read_bytes() == b"corrupt" and not (tmp_path / "complete").exists()


def test_full_independent_roundtrip_reconciles_but_does_not_accept_deletion(tmp_path, monkeypatch):
    stages, quarantine = prepared(tmp_path, monkeypatch)
    box = Box()
    monkeypatch.setattr(finalizer.archive_host_catalog, "destination_from_environment", lambda: box)

    def read(key, target, limit, **_):
        body = box.bodies[key]
        assert len(body) <= limit
        target.write_bytes(body)

    reader = SimpleNamespace(identity=box.identity, download_catalog=read, close=lambda: None)
    monkeypatch.setattr(
        "app.raw_archive.read_only_box.configured_catalog_read_only_box", lambda: reader
    )
    output = tmp_path / "complete"
    result = finalizer.finalize(stages, quarantine, output)
    assert result["complete_catalog"] and result["independent_restoration"]
    assert result["deletion_readiness"] is False and result["remote_full_cohort_readback"] is False
    assert result["deleted_files"] == 0
    assert (output / "readback/wav-catalog.sqlite3").is_file()
    assert (output / "reconciliation/reconciliation.json").is_file()
    assert json.loads((output / "verified.json").read_text())["complete_catalog"] is True
    original = next(stages["gateway"].glob("*.gz"))
    copied = output / "bundle" / original.name
    assert original.stat().st_ino != copied.stat().st_ino


def test_readback_corruption_never_writes_complete_proof(tmp_path, monkeypatch):
    stages, quarantine = prepared(tmp_path, monkeypatch)
    box = Box()
    monkeypatch.setattr(finalizer.archive_host_catalog, "destination_from_environment", lambda: box)
    reader = SimpleNamespace(
        identity=box.identity,
        download_catalog=lambda _, p, *a, **kw: p.write_bytes(b"bad"),
        close=lambda: None,
    )
    monkeypatch.setattr(
        "app.raw_archive.read_only_box.configured_catalog_read_only_box", lambda: reader
    )
    output = tmp_path / "complete"
    with pytest.raises(ArchiveBlocked, match="readback mismatch"):
        finalizer.finalize(stages, quarantine, output)
    assert not (output / "verified.json").exists()
    assert (output / "bundle/manifest.json").is_file()
