"""Source fallback retains the same reserve and verifies through a separate reader."""

import json
import os
import shutil
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from app.raw_archive import source_catalog_once as worker
from app.raw_archive.health import SafetyPause
from app.raw_archive.policy import ArchiveBlocked

from .test_archive_host_catalog import Box, Probe
from .test_wav_catalog import fixture_bundle


@pytest.fixture
def configured(monkeypatch, tmp_path):
    for key, value in {
        "RAW_ARCHIVE_ENVIRONMENT": "production",
        "RAW_ARCHIVE_COORDINATION_DIR": "/var/lib/greenmind-archive-coordination",
        "RAW_ARCHIVE_ENABLED": "false",
        "RAW_ARCHIVE_DELETE_ENABLED": "false",
        "RETENTION_ENABLED": "false",
        "DIRECT_RETENTION_ENABLED": "false",
        "VISUAL_PRUNE_ENABLED": "false",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("RAW_ARCHIVE_WORKER_ROLE", raising=False)
    monkeypatch.setattr(worker.os, "uname", lambda: SimpleNamespace(nodename="Biolingo"))
    monkeypatch.setattr(worker.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=os.geteuid()))
    monkeypatch.setattr(worker, "lease", lambda _: nullcontext())
    monkeypatch.setattr("sys.argv", ["catalog", "--output", str(tmp_path / "attempt")])


def test_memory_pause_opens_no_source_or_upload(tmp_path, monkeypatch, configured):
    probe = Probe(False)
    monkeypatch.setattr(worker, "health_probe", lambda: probe)
    monkeypatch.setattr(worker, "backup_ledger", lambda *a, **kw: pytest.fail("No journal read"))
    monkeypatch.setattr(worker, "create_engine", lambda *a, **kw: pytest.fail("No SQL"))
    monkeypatch.setattr(worker, "destination_from_environment", lambda: pytest.fail("No upload"))
    with pytest.raises(SafetyPause):
        worker.main()
    assert not (tmp_path / "attempt").exists() and probe.closed


def test_wrong_account_opens_no_outputs(tmp_path, monkeypatch, configured):
    monkeypatch.setattr(worker.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=os.geteuid() + 1))
    with pytest.raises(ArchiveBlocked):
        worker.main()
    assert not (tmp_path / "attempt").exists()


def test_destructive_flag_rejected_before_any_io(tmp_path, monkeypatch, configured):
    monkeypatch.setenv("VISUAL_PRUNE_ENABLED", "true")
    with pytest.raises(ArchiveBlocked, match="disabled"):
        worker.main()
    assert not (tmp_path / "attempt").exists()


def test_full_catalog_offsite_roundtrip_keeps_source_and_no_deletion_ready(
    tmp_path, monkeypatch, configured, capsys
):
    fixture, manifest = fixture_bundle(tmp_path)
    probe, box = Probe(True), Box()
    monkeypatch.setattr(worker, "health_probe", lambda: probe)

    def backup(_, target, **kw):
        shutil.copyfile(tmp_path / "source/archive.sqlite3", target)
        return {"sha256": worker.catalog.checksum(target), "bytes": target.stat().st_size}

    monkeypatch.setattr(worker, "backup_ledger", backup)
    monkeypatch.setattr(
        worker,
        "metadata_source_settings",
        lambda *_: {
            "database": "postgresql://greenmind_wav_metadata_b4ae52d674aa@127.0.0.1/archive",
            "bucket": "greenmind-direct-production-hotspot",
        },
    )
    monkeypatch.setattr(
        worker, "create_engine", lambda *a, **kw: SimpleNamespace(dispose=lambda: None)
    )

    def export(_, kind, output, checkpoint, **kw):
        checkpoint()
        parts = [entry for entry in manifest["files"] if entry["kind"] == kind]
        for entry in parts:
            shutil.copyfile(fixture / entry["file"], output / entry["file"])
        return parts, manifest["tables"][kind]

    monkeypatch.setattr(worker.catalog, "export_metadata", export)
    monkeypatch.setattr(worker, "destination_from_environment", lambda: box)

    def read(key, target, limit, **kw):
        body = box.bodies[key]
        assert len(body) <= limit
        target.write_bytes(body)

    monkeypatch.setattr(
        worker,
        "configured_catalog_read_only_box",
        lambda: SimpleNamespace(identity=box.identity, download_catalog=read, close=lambda: None),
    )
    worker.main()
    proof = json.loads(capsys.readouterr().out)
    assert proof["tables"] == manifest["tables"]
    assert proof["complete"] and proof["independent_read_identity"]
    assert proof["deleted_files"] == 0 and proof["deletion_readiness"] is False
    assert (tmp_path / "source/archive.sqlite3").is_file()
    assert (tmp_path / "attempt/verified.json").exists()
    assert box.closed and probe.closed
