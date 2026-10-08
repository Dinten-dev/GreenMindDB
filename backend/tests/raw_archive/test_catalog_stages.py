"""Completed source stages resume without SQL; final manifests require full restore."""

import json
import os
from datetime import UTC, datetime, timedelta

import pytest

from app.raw_archive import catalog_stages as stages
from app.raw_archive import wav_catalog as catalog
from app.raw_archive.policy import ArchiveBlocked

from .test_wav_catalog import fixture_bundle


def fixture_stages(tmp_path):
    root, manifest = fixture_bundle(tmp_path)
    result = {}
    for kind in stages.KINDS:
        folder = tmp_path / kind
        folder.mkdir(mode=0o700)
        files = [entry for entry in manifest["files"] if entry["kind"] == kind]
        for entry in files:
            os.link(root / entry["file"], folder / entry["file"])
        stage = stages.completed_stage(
            kind,
            datetime.now(UTC).isoformat(),
            files,
            manifest["tables"].get(kind),
            direct_bucket="greenmind-direct-production-hotspot",
        )
        if kind == "ledger":
            stage.update(
                source_sha256=catalog.checksum(tmp_path / "source/archive.sqlite3"),
                processed_at=datetime.now(UTC).isoformat(),
            )
        catalog.save_json(folder / "stage.json", stage)
        result[kind] = folder
    return result


def test_full_local_restore_precedes_final_manifest(tmp_path):
    inputs = fixture_stages(tmp_path)
    target = tmp_path / "complete"
    result = stages.assemble(inputs, target)
    manifest = json.loads((target / "manifest.json").read_text())
    assert result["complete"] and (target / "local-restore.sqlite3").exists()
    assert manifest["cross_database_atomic"] is False
    assert set(manifest["source_windows"]) == set(stages.KINDS)
    assert manifest["created_at"] == min(
        entry["started_at"] for entry in manifest["source_windows"].values()
    )
    assert all((folder / "stage.json").exists() for folder in inputs.values())


def test_completed_stage_reuse_never_opens_source_connections(tmp_path, monkeypatch, capsys):
    from app.raw_archive import isolated_catalog

    inputs = fixture_stages(tmp_path)
    monkeypatch.setattr(isolated_catalog, "load_credentials", lambda _: pytest.fail("credentials"))
    monkeypatch.setattr(isolated_catalog, "create_engine", lambda *a, **kw: pytest.fail("SQL"))
    monkeypatch.setattr(
        "sys.argv",
        [
            "catalog",
            "--kind",
            "gateway",
            "--session",
            "a" * 24,
            "--credentials",
            str(tmp_path / "absent"),
            "--output",
            str(inputs["gateway"]),
        ],
    )
    isolated_catalog.main()
    assert json.loads(capsys.readouterr().out)["status"] == "reused"


def test_corrupted_stage_cannot_be_reused_or_published(tmp_path):
    inputs = fixture_stages(tmp_path)
    entry = stages.load_stage(inputs["gateway"], "gateway")["files"][0]
    (inputs["gateway"] / entry["file"]).write_bytes(b"corrupted")
    target = tmp_path / "incomplete"
    with pytest.raises(ArchiveBlocked, match="changed"):
        stages.assemble(inputs, target)
    assert not (target / "manifest.json").exists()


def test_restore_failure_has_no_completion_manifest(tmp_path):
    inputs = fixture_stages(tmp_path)
    path = inputs["gateway"] / "stage.json"
    stage = json.loads(path.read_text())
    stage["files"][0]["rows"] += 1
    path.write_text(json.dumps(stage))
    with pytest.raises(ArchiveBlocked, match="rows"):
        stages.assemble(inputs, tmp_path / "incomplete")
    assert not (tmp_path / "incomplete" / "manifest.json").exists()


def test_expired_sql_window_and_missing_table_are_rejected(tmp_path):
    inputs = fixture_stages(tmp_path)
    stage = stages.load_stage(inputs["gateway"], "gateway")
    stage["finished_at"] = (
        datetime.fromisoformat(stage["started_at"]) + timedelta(seconds=121)
    ).isoformat()
    with pytest.raises(ArchiveBlocked, match="two-minute"):
        stages.validate_stage(stage, "gateway", inputs["gateway"])
    stage["finished_at"] = stage["started_at"]
    del stage["tables"]["wav_file"]
    with pytest.raises(ArchiveBlocked, match="omits"):
        stages.validate_stage(stage, "gateway", inputs["gateway"])


@pytest.mark.parametrize("value", [0, 1001])
def test_mac_fetch_budget_is_bounded(tmp_path, value):
    with pytest.raises(ArchiveBlocked, match="cursor batch"):
        catalog.export_metadata(None, "gateway", tmp_path, lambda: None, fetch_rows=value)


def test_source_journal_time_cannot_be_replaced_by_local_processing(tmp_path, monkeypatch):
    from app.raw_archive import isolated_catalog
    from app.raw_archive.policy import checksum

    fixture_bundle(tmp_path)
    database = tmp_path / "source/archive.sqlite3"
    at = datetime.now(UTC) - timedelta(days=2)
    proof = tmp_path / "backup-proof.json"
    catalog.save_json(
        proof,
        {
            "schema": 1,
            "environment": "production",
            "complete": True,
            "created_by_uid": 0,
            "started_at": at.isoformat(),
            "finished_at": (at + timedelta(seconds=1)).isoformat(),
            "bytes": database.stat().st_size,
            "sha256": checksum(database),
        },
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "catalog",
            "--kind",
            "ledger",
            "--ledger",
            str(database),
            "--ledger-proof",
            str(proof),
            "--output",
            str(tmp_path / "stage"),
        ],
    )
    isolated_catalog.main()
    captured = stages.load_stage(tmp_path / "stage", "ledger")
    assert captured["started_at"] == at.isoformat()
    assert captured["finished_at"] == (at + timedelta(seconds=1)).isoformat()
    assert datetime.fromisoformat(captured["processed_at"]) > at + timedelta(days=1)
    database.write_bytes(b"changed")
    with pytest.raises(ArchiveBlocked, match="differs"):
        stages.ledger_proof(proof, database)


def test_old_ledger_stage_without_provenance_cannot_be_reused(tmp_path):
    inputs = fixture_stages(tmp_path)
    path = inputs["ledger"] / "stage.json"
    stage = json.loads(path.read_text())
    del stage["source_sha256"]
    path.write_text(json.dumps(stage))
    with pytest.raises(ArchiveBlocked, match="provenance"):
        stages.load_stage(inputs["ledger"], "ledger")


def test_combined_export_requires_journal_provenance_before_connections(tmp_path, monkeypatch):
    from app.raw_archive import isolated_catalog

    monkeypatch.setattr(isolated_catalog, "load_credentials", lambda _: pytest.fail("credentials"))
    monkeypatch.setattr(isolated_catalog, "create_engine", lambda *a, **kw: pytest.fail("SQL"))
    monkeypatch.setattr(
        "sys.argv",
        [
            "catalog",
            "--session",
            "a" * 24,
            "--credentials",
            str(tmp_path / "absent"),
            "--ledger",
            str(tmp_path / "ledger"),
            "--output",
            str(tmp_path / "output"),
        ],
    )
    with pytest.raises(ArchiveBlocked, match="provenance"):
        isolated_catalog.main()
