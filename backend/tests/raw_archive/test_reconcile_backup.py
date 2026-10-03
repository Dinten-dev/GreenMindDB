import gzip
import hashlib
import json
import sqlite3
from datetime import UTC, datetime

from app.raw_archive.policy import checksum
from app.raw_archive.reconcile_backup import reconcile


def test_full_fixed_backup_reconciliation_counts_invalid_and_pending_without_ram_map(tmp_path):
    journal = tmp_path / "journal.sqlite3"
    db = sqlite3.connect(journal)
    db.execute("CREATE TABLE archive(id TEXT PRIMARY KEY,state TEXT,receipt TEXT)")
    digest = "a" * 64
    key = "verified.wav"
    identity = hashlib.sha256(
        json.dumps(["gateway", "greenmind-raw", key], separators=(",", ":")).encode()
    ).hexdigest()
    stamp = datetime(2026, 9, 1, tzinfo=UTC).isoformat()
    receipt = {
        "recording": {
            "kind": "gateway",
            "bucket": "greenmind-raw",
            "key": key,
            "sha256": digest,
            "size": 7,
            "received_at": stamp,
        },
        "first_verified_at": stamp,
    }
    db.execute("INSERT INTO archive VALUES (?,?,?)", (identity, "verified", json.dumps(receipt)))
    db.commit()
    db.close()
    gateway = tmp_path / "fixture-gateway.jsonl.gz"
    with gzip.open(gateway, "wt") as body:
        for source_key, source_digest in [
            (key, digest),
            ("bad.wav", "invalid_checksum"),
            ("pending.wav", "b" * 64),
        ]:
            body.write(
                json.dumps(
                    {
                        "table": "wav_file",
                        "row": {
                            "raw_deleted_at": None,
                            "s3_key": source_key,
                            "content_sha256": source_digest,
                            "file_size_bytes": 7,
                            "ended_at": stamp,
                        },
                    }
                )
                + "\n"
            )
    direct = tmp_path / "fixture-direct.jsonl.gz"
    with gzip.open(direct, "wt") as body:
        body.write(json.dumps({"table": "direct_device", "row": {"id": "fixture"}}) + "\n")
    manifest = {
        "created_at": "2026-10-03T00:00:00+00:00",
        "files": [
            {
                "file": path.name,
                "sha256": checksum(path),
                "bytes": path.stat().st_size,
                "rows": rows,
            }
            for path, rows in ((journal, 1), (gateway, 3), (direct, 1))
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    result = reconcile(path, tmp_path / "report")
    counts = result["counts"]["gateway"]
    assert counts["files"] == 3 and counts["matched"] == 1 and counts["pending"] == 1
    assert counts["invalid_metadata"] == 1 and counts["seven_day_reserve_elapsed"] == 1
    assert result["complete_fixed_backup_scan"] and not result["deletion_authorized"]
