"""Offline phases cannot combine snapshots or claim incomplete tables complete."""

import json
from types import SimpleNamespace

import pytest

from app.raw_archive import catalog_offline as offline
from app.raw_archive import wav_catalog
from app.raw_archive.policy import ArchiveBlocked

from .test_catalog_capture import completed_fixture, framing


@pytest.fixture(autouse=True)
def disk(monkeypatch):
    monkeypatch.setattr(
        offline.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(total=100 * 1024**3, free=10 * 1024**3),
    )


def stages(tmp_path):
    source, proof = completed_fixture(tmp_path)
    tables = {}
    for table in wav_catalog.COLUMNS["gateway"]:
        output = tmp_path / table
        offline.process_table(source, table, output)
        tables[table] = output
    return source, proof, tables


def test_all_offline_phases_keep_one_original_source_window(tmp_path):
    source, proof, tables = stages(tmp_path)
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    result = offline.assemble(source, tables, tmp_path / "complete")
    assert result["started_at"] == proof["started_at"]
    assert result["finished_at"] == proof["finished_at"]
    assert result["capture_sha256"] == wav_catalog.checksum(source / "capture.json")
    assert sum(result["tables"].values()) == 1
    assert set(result["tables"]) == set(wav_catalog.COLUMNS["gateway"])
    assert before == {p.name: p.read_bytes() for p in source.iterdir()}
    assert (tmp_path / "complete/stage.json").exists()


@pytest.mark.parametrize(
    "change", ["missing_table", "mixed_capture", "missing_rows", "unsafe_part", "corrupt_part"]
)
def test_incomplete_or_changed_phases_never_publish_stage(tmp_path, change):
    source, _, tables = stages(tmp_path)
    output = tmp_path / "complete"
    if change == "missing_table":
        tables.pop("sensor")
    else:
        folder = tables["organization"]
        info = folder / "table.json"
        phase = json.loads(info.read_text())
        if change == "mixed_capture":
            phase["capture_sha256"] = "0" * 64
        elif change == "missing_rows":
            phase["files"] = []
        elif change == "unsafe_part":
            phase["files"][0]["file"] = "../outside.gz"
        elif change == "corrupt_part":
            (folder / phase["files"][0]["file"]).write_bytes(b"changed")
        info.write_text(json.dumps(phase))
    with pytest.raises(ArchiveBlocked):
        offline.assemble(source, tables, output)
    assert not (output / "stage.json").exists()
    assert (source / "capture.json").exists()


def test_failed_source_guard_never_creates_phase(tmp_path):
    source, _ = completed_fixture(tmp_path)
    output = tmp_path / "phase"

    def pause():
        raise ArchiveBlocked("guard")

    with pytest.raises(ArchiveBlocked, match="guard"):
        offline.process_table(source, "organization", output, pause)
    assert not output.exists()


def test_failed_scientific_validation_cannot_complete_table(tmp_path):
    source, proof = completed_fixture(tmp_path)
    table = "wav_feature_version"
    fields = wav_catalog.COLUMNS["gateway"][table]
    entry = next(e for e in proof["files"] if e["table"] == table)
    values = [None] * len(fields)
    values[fields.index("feature_payload")] = b'{"email":"private"}'
    path = source / entry["file"]
    path.write_bytes(framing([values]))
    entry.update(rows=1, bytes=path.stat().st_size, sha256=wav_catalog.checksum(path))
    entry["types"][fields.index("feature_payload")] = "jsonb"
    (source / "capture.json").write_text(json.dumps(proof))
    output = tmp_path / "phase"
    with pytest.raises(ArchiveBlocked):
        offline.process_table(source, table, output)
    assert not (output / "table.json").exists()
    assert path.exists()


def test_unbounded_capture_window_is_rejected(tmp_path):
    source, proof = completed_fixture(tmp_path)
    proof["finished_at"] = "2026-10-10T10:02:01+00:00"
    (source / "capture.json").write_text(json.dumps(proof))
    with pytest.raises(ArchiveBlocked, match="budget"):
        offline.process_table(source, "organization", tmp_path / "phase")
    assert not (tmp_path / "phase").exists()
