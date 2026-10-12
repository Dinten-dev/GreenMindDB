"""Complete streaming reconciliation of a verified fixed catalog backup, not live SQL."""

import argparse
import gzip
import hashlib
import json
import os
import re
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .policy import ArchiveBlocked, checksum
from .recovery import private_directory


def reconcile(manifest_path, output):
    private_directory(output)
    manifest = json.loads(manifest_path.read_text())
    entries = manifest["files"]
    if len(entries) != 3:
        raise ArchiveBlocked("Complete journal and both catalog backups required")
    files, expected_rows = {}, {}
    for entry in entries:
        name = entry["file"]
        if Path(name).name != name:
            raise ArchiveBlocked("Unsafe local backup filename")
        path = manifest_path.parent / name
        if (
            path.is_symlink()
            or checksum(path) != entry["sha256"]
            or path.stat().st_size != entry["bytes"]
        ):
            raise ArchiveBlocked("Catalog backup does not match its manifest")
        kind = (
            "ledger"
            if name.endswith(".sqlite3")
            else "gateway"
            if name.endswith("-gateway.jsonl.gz")
            else "direct"
        )
        if kind in files:
            raise ArchiveBlocked("Duplicate catalog source")
        files[kind] = path
        expected_rows[kind] = entry["rows"]
    if set(files) != {"ledger", "gateway", "direct"}:
        raise ArchiveBlocked("Incomplete catalog source set")
    ledger = sqlite3.connect(files["ledger"].as_uri() + "?mode=ro", uri=True, timeout=2)
    index_path = output / "reconcile-index.sqlite3"
    descriptor = os.open(index_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    index = sqlite3.connect(index_path)
    ledger.execute("PRAGMA query_only=ON")
    ledger.execute("PRAGMA cache_size=-1024")
    index.execute("PRAGMA cache_size=-2048")
    index.execute("CREATE TABLE segments(id TEXT PRIMARY KEY,published INTEGER,sealed INTEGER)")
    counts = {
        kind: {
            "files": 0,
            "bytes": 0,
            "matched": 0,
            "matched_bytes": 0,
            "pending": 0,
            "pending_bytes": 0,
            "invalid_metadata": 0,
            "receipt_mismatches": 0,
            "seven_day_reserve_elapsed": 0,
        }
        for kind in ("gateway", "direct")
    }
    cutoff = datetime.fromisoformat(manifest["created_at"]) - timedelta(days=7)
    started = time.monotonic()

    def accept(kind, bucket, key, digest, size, ended):
        counter = counts[kind]
        counter["files"] += 1
        counter["bytes"] += size if type(size) is int and size > 0 else 0
        if (
            not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or type(size) is not int
            or size <= 0
        ):
            counter["invalid_metadata"] += 1
            return
        archive_id = hashlib.sha256(
            json.dumps([kind, bucket, key], separators=(",", ":")).encode()
        ).hexdigest()
        saved = ledger.execute(
            "SELECT state,receipt FROM archive WHERE id=?", (archive_id,)
        ).fetchone()
        receipt = json.loads(saved[1]) if saved and saved[0] == "verified" else None
        record = receipt.get("recording", {}) if receipt else {}
        matched = tuple(
            record.get(field) for field in ("kind", "bucket", "key", "sha256", "size")
        ) == (kind, bucket, key, digest, size)
        label = "matched" if matched else "pending"
        counter[label] += 1
        counter[label + "_bytes"] += size
        if receipt and not matched:
            counter["receipt_mismatches"] += 1
        if matched:
            stamps = [
                ended,
                record.get("received_at"),
                receipt.get("first_verified_at", receipt.get("verified_at")),
            ]
            if all(value and datetime.fromisoformat(value) <= cutoff for value in stamps):
                counter["seven_day_reserve_elapsed"] += 1

    try:
        if ledger.execute("SELECT count(*) FROM archive").fetchone()[0] != expected_rows["ledger"]:
            raise ArchiveBlocked("Journal row count differs from backup manifest")
        for kind in ("gateway", "direct"):
            scanned = 0
            with gzip.open(files[kind], "rt") as body:
                for position, line in enumerate(body):
                    scanned += 1
                    if position % 1000 == 0 and time.monotonic() - started > 120:
                        raise ArchiveBlocked("Offline reconciliation exceeded its time budget")
                    entry = json.loads(line)
                    table, row = entry["table"], entry["row"]
                    if kind == "gateway" and table == "wav_file" and row["raw_deleted_at"] is None:
                        accept(
                            kind,
                            "greenmind-raw",
                            row["s3_key"],
                            row["content_sha256"],
                            row["file_size_bytes"],
                            row["ended_at"],
                        )
                    elif kind == "direct" and table == "direct_segment":
                        index.execute(
                            "INSERT INTO segments VALUES (?,?,?)",
                            (row["id"], row["published_revision"], row["sealed"]),
                        )
                    elif (
                        kind == "direct"
                        and table == "direct_revision"
                        and row["raw_deleted_at"] is None
                        and row["verified_at"]
                    ):
                        segment = index.execute(
                            "SELECT published,sealed FROM segments WHERE id=?", (row["segment_id"],)
                        ).fetchone()
                        if not segment or segment != (row["revision"], 1):
                            continue
                        payload = row["manifest"]
                        digest = hashlib.sha256(
                            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
                        ).hexdigest()
                        if digest != row["manifest_sha256"]:
                            raise ArchiveBlocked("Direct manifest checksum mismatch")
                        cfg = payload["config"]
                        for run in payload["runs"]:
                            size = (
                                44 + run["frame_count"] * cfg["channels"] * cfg["sample_bits"] // 8
                            )
                            ended = datetime.fromtimestamp(
                                run["started_at_us"] / 1_000_000
                                + run["frame_count"] / cfg["sample_rate"],
                                UTC,
                            ).isoformat()
                            accept(
                                kind,
                                os.environ["RAW_ARCHIVE_DIRECT_S3_BUCKET"],
                                run["key"],
                                run["sha256"],
                                size,
                                ended,
                            )
            if scanned != expected_rows[kind]:
                raise ArchiveBlocked("Catalog row count differs from backup manifest")
    finally:
        ledger.close()
        index.close()
    report = {
        "complete_fixed_backup_scan": True,
        "counts": counts,
        "catalog_manifest_sha256": checksum(manifest_path),
        "deleted_files": 0,
        "live_arrivals_after_backup_excluded": True,
        "physical_remote_readback_scope": "receipts only; independent snapshot WAV readback still required",
        "deletion_authorized": False,
    }
    with (output / "reconciliation.json").open("x") as body:
        os.chmod(body.name, 0o600)
        json.dump(report, body, indent=2)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(reconcile(args.manifest, args.output)))


if __name__ == "__main__":
    main()
