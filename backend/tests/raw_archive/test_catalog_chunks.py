"""Chunking cannot mix snapshots or conceal gaps, NULLs or corrupt bytes."""

import json
from types import SimpleNamespace

import pytest

from app.raw_archive import catalog_chunks as chunks
from app.raw_archive import wav_catalog
from app.raw_archive.policy import ArchiveBlocked

from .test_catalog_capture import completed_fixture, framing


@pytest.fixture(autouse=True)
def disk(monkeypatch):
    monkeypatch.setattr(
        chunks.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(total=100 * 1024**3, free=20 * 1024**3),
    )


def source_rows(tmp_path):
    source, proof = completed_fixture(tmp_path)
    path = source / "organization.pgcopy"
    path.write_bytes(framing([[b"a"], [None], [b""], [b'quote,"\ntext'], [b"e"]]))
    item = next(e for e in proof["files"] if e["table"] == "organization")
    item.update(rows=5, bytes=path.stat().st_size, sha256=wav_catalog.checksum(path))
    (source / "capture.json").write_text(json.dumps(proof))
    return source


def test_chunks_keep_exact_original_rows_and_table_provenance(tmp_path, monkeypatch):
    source = source_rows(tmp_path)
    original = {p.name: p.read_bytes() for p in source.iterdir()}
    monkeypatch.setattr(chunks, "MAX_ROWS", 2)
    raw = tmp_path / "chunks"
    result = chunks.prepare(source, "organization", raw)
    assert [p["rows"] for p in result["chunks"]] == [2, 2, 1]
    phases = []
    for position in range(3):
        folder = tmp_path / f"phase-{position}"
        chunks.process(source, raw, position, folder)
        phases.append(folder)
    table = chunks.assemble(source, raw, phases, tmp_path / "table")
    assert table["rows"] == 5 and table["scientific_validation_complete"] is True
    assert table["capture_sha256"] == wav_catalog.checksum(source / "capture.json")
    assert sum(p["rows"] for p in table["files"]) == 5
    assert original == {p.name: p.read_bytes() for p in source.iterdir()}


@pytest.mark.parametrize("change", ["gap", "overlap", "missing", "mixed", "bytes"])
def test_changed_coverage_cannot_publish(tmp_path, monkeypatch, change):
    source = source_rows(tmp_path)
    monkeypatch.setattr(chunks, "MAX_ROWS", 2)
    raw = tmp_path / "chunks"
    result = chunks.prepare(source, "organization", raw)
    if change == "gap":
        result["chunks"][1]["first_row"] += 1
    elif change == "overlap":
        result["chunks"][1]["start"] -= 1
    elif change == "missing":
        result["chunks"].pop()
    elif change == "mixed":
        result["capture_sha256"] = "0" * 64
    else:
        result["source_bytes"] += 1
    (raw / "chunks.json").write_text(json.dumps(result))
    with pytest.raises(ArchiveBlocked):
        chunks.process(source, raw, 0, tmp_path / "invalid")
    assert not (tmp_path / "invalid/validated.json").exists()


def test_corrupt_chunk_rejected(tmp_path):
    source = source_rows(tmp_path)
    raw = tmp_path / "chunks"
    result = chunks.prepare(source, "organization", raw)
    (raw / result["chunks"][0]["file"]).write_bytes(b"corrupt")
    with pytest.raises(ArchiveBlocked):
        chunks.process(source, raw, 0, tmp_path / "invalid")


def test_empty_table_stays_explicitly_zero(tmp_path):
    source, _ = completed_fixture(tmp_path)
    raw = tmp_path / "chunks"
    result = chunks.prepare(source, "zone", raw)
    assert result["rows"] == 0
    phase = tmp_path / "phase"
    chunks.process(source, raw, 0, phase)
    table = chunks.assemble(source, raw, [phase], tmp_path / "table")
    assert table["rows"] == 0 and table["files"] == []


def test_mixed_validated_chunk_cannot_complete_table(tmp_path):
    source = source_rows(tmp_path)
    raw, phase = tmp_path / "chunks", tmp_path / "phase"
    chunks.prepare(source, "organization", raw)
    result = chunks.process(source, raw, 0, phase)
    result["chunks_sha256"] = "0" * 64
    (phase / "validated.json").write_text(json.dumps(result))
    with pytest.raises(ArchiveBlocked):
        chunks.assemble(source, raw, [phase], tmp_path / "table")
    assert not (tmp_path / "table/table.json").exists()
