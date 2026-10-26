"""Fixed-cohort reconciliation covers actual sanitized catalog and journal bodies."""

import gzip
import json

import pytest

from app.raw_archive.policy import ArchiveBlocked, checksum
from app.raw_archive.wav_catalog import COLUMNS, raw_json, save_json
from app.raw_archive.wav_reconcile import reconcile

from .test_wav_catalog import fixture_bundle


def inventory(tmp_path):
    root, manifest = fixture_bundle(tmp_path)
    ledger_part = root / manifest["files"][0]["file"]
    with gzip.open(ledger_part, "rb") as body:
        receipt = json.loads(body.readline())["receipt"]
    record = receipt["recording"]
    row = dict.fromkeys(COLUMNS["gateway"]["wav_file"])
    row.update(
        id=record["identity"],
        s3_key=record["key"],
        content_sha256=record["sha256"],
        file_size_bytes=record["size"],
        ended_at=record["ended_at"],
        created_at=record["received_at"],
    )
    entry = next(entry for entry in manifest["files"] if entry["kind"] == "gateway")
    path = root / entry["file"]
    with gzip.open(path, "rb") as body:
        existing = body.read()
    payload = existing + raw_json({"table": "wav_file", "row": row}) + b"\n"
    with gzip.open(path, "wb") as body:
        body.write(payload)
    entry.update(
        sha256=checksum(path),
        bytes=path.stat().st_size,
        rows=entry["rows"] + 1,
        decoded_bytes=len(payload),
    )
    manifest["tables"]["gateway"]["wav_file"] = 1
    save_json(root / "manifest.json", manifest)
    return root / "manifest.json", record


def test_complete_cohort_matches_verified_receipt_without_claiming_remote_readback(tmp_path):
    manifest, _ = inventory(tmp_path)
    result = reconcile(manifest, tmp_path / "report")
    assert result["complete"] and result["counts"]["gateway"]["matched"] == 1
    assert result["eligible_pending_files"] == result["receipt_mismatches"] == 0
    assert result["remote_physical_readback"] is False and result["deleted_files"] == 0


def test_explicit_quarantine_keeps_source_outside_eligible_set(tmp_path):
    manifest, record = inventory(tmp_path)
    result = reconcile(manifest, tmp_path / "report", excluded=[("gateway", record["identity"])])
    assert result["counts"]["gateway"]["excluded"] == 1
    assert result["counts"]["gateway"]["matched"] == 0


def test_changed_inventory_is_not_accepted(tmp_path):
    manifest, _ = inventory(tmp_path)
    body = json.loads(manifest.read_text())
    (manifest.parent / body["files"][0]["file"]).write_bytes(b"changed")
    with pytest.raises(ArchiveBlocked, match="mismatch"):
        reconcile(manifest, tmp_path / "report")
