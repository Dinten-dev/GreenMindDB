"""Fixed WAV-only catalog reconciliation; journal receipts are not remote readbacks."""

import argparse
import hashlib
import json
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .policy import ArchiveBlocked, checksum
from .wav_catalog import manifest_check, private_directory, raw_json, restore_parts, save_json


def reconcile(manifest_path, output, *, excluded=(), checkpoint=lambda: None):
    private_directory(output)
    manifest = json.loads(manifest_path.read_text())
    manifest_check(manifest)
    files = {entry["file"]: manifest_path.parent / entry["file"] for entry in manifest["files"]}
    index = output / "catalog.sqlite3"
    restore_parts(manifest, files, index, checkpoint)
    cutoff = datetime.fromisoformat(manifest["created_at"])
    if cutoff.tzinfo is None:
        raise ArchiveBlocked("Fixed inventory cutoff must include a timezone")
    excluded = set(excluded)
    counts = {
        kind: {
            "files": 0,
            "bytes": 0,
            "matched": 0,
            "pending": 0,
            "pending_bytes": 0,
            "receipt_mismatches": 0,
            "invalid_metadata": 0,
            "excluded": 0,
            "eligible_pending_files": 0,
            "seven_day_reserve_elapsed": 0,
        }
        for kind in ("gateway", "direct")
    }
    db = sqlite3.connect(index.as_uri() + "?mode=ro", uri=True, timeout=2)
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA cache_size=-2048")
    started = time.monotonic()

    def guard():
        checkpoint()
        if time.monotonic() - started > 120:
            raise ArchiveBlocked("Fixed reconciliation reached its time budget")

    def accept(kind, identity, bucket, key, digest, size, ended, received):
        c = counts[kind]
        c["files"] += 1
        c["bytes"] += size if type(size) is int and size > 0 else 0
        if (kind, identity) in excluded:
            c["excluded"] += 1
            return
        if not isinstance(digest, str) or len(digest) != 64 or type(size) is not int or size <= 0:
            c["invalid_metadata"] += 1
            return
        ident = hashlib.sha256(raw_json([kind, bucket, key])).hexdigest()
        saved = db.execute("SELECT state,receipt FROM archive WHERE id=?", (ident,)).fetchone()
        receipt = json.loads(saved[1]) if saved and saved[0] == "verified" else None
        record = receipt.get("recording", {}) if receipt else {}
        matched = tuple(
            record.get(field) for field in ("kind", "bucket", "key", "sha256", "size")
        ) == (kind, bucket, key, digest, size)
        c["matched" if matched else "pending"] += 1
        if not matched:
            c["pending_bytes"] += size
        if receipt and not matched:
            c["receipt_mismatches"] += 1
        # Missing receipts cannot satisfy the post-verification reserve; separately
        # report every older source as pending rather than silently calling it safe.
        stamps = [ended, received]
        if receipt:
            stamps.append(
                datetime.fromisoformat(receipt.get("first_verified_at", receipt["verified_at"]))
            )
        if max(stamps) + timedelta(days=7) <= cutoff:
            c["seven_day_reserve_elapsed"] += int(matched)
            c["eligible_pending_files"] += int(not matched)

    try:
        for position, (body,) in enumerate(
            db.execute(
                "SELECT row FROM records WHERE kind='gateway' AND table_name='wav_file' ORDER BY ordinal"
            )
        ):
            if position % 100 == 0:
                guard()
            row = json.loads(body)
            if row["raw_deleted_at"] is None:
                accept(
                    "gateway",
                    row["id"],
                    "greenmind-raw",
                    row["s3_key"],
                    row["content_sha256"],
                    row["file_size_bytes"],
                    datetime.fromisoformat(row["ended_at"]),
                    datetime.fromisoformat(row["created_at"]),
                )
        # SQLite disk index avoids accumulating all Direct segment identities in RAM.
        db.close()
        db = sqlite3.connect(index)
        db.execute("PRAGMA cache_size=-2048")
        db.execute(
            "CREATE TABLE segments(id TEXT PRIMARY KEY,published INTEGER,sealed INTEGER,updated REAL)"
        )
        for position, (body,) in enumerate(
            db.execute(
                "SELECT row FROM records WHERE kind='direct' AND table_name='direct_segment' ORDER BY ordinal"
            )
        ):
            if position % 100 == 0:
                guard()
            row = json.loads(body)
            db.execute(
                "INSERT INTO segments VALUES (?,?,?,?)",
                (row["id"], row["published_revision"], row["sealed"], row["updated_at"]),
            )
        db.commit()
        db.execute("PRAGMA query_only=ON")
        for position, (body,) in enumerate(
            db.execute(
                "SELECT row FROM records WHERE kind='direct' AND table_name='direct_revision' ORDER BY ordinal"
            )
        ):
            if position % 100 == 0:
                guard()
            row = json.loads(body)
            segment = db.execute(
                "SELECT published,sealed,updated FROM segments WHERE id=?", (row["segment_id"],)
            ).fetchone()
            if (
                not segment
                or segment[:2] != (row["revision"], 1)
                or row["raw_deleted_at"] is not None
            ):
                continue
            config = row["manifest"]["config"]
            bucket = row["manifest"].get("archive_bucket")
            # Source bucket is supplied as non-secret catalog context, never guessed.
            bucket = bucket or manifest.get("direct_bucket")
            if not bucket or not bucket.startswith("greenmind-direct-" + manifest["environment"]):
                raise ArchiveBlocked("Exact Direct source bucket missing from fixed catalog")
            for run in row["manifest"]["runs"]:
                ended = max(
                    run["started_at_us"] / 1e6 + run["frame_count"] / config["sample_rate"],
                    (row["manifest"]["bucket_start_us"] / 1e6) + 600,
                )
                accept(
                    "direct",
                    row["segment_id"] + ":" + str(row["revision"]),
                    bucket,
                    run["key"],
                    run["sha256"],
                    44 + run["frame_count"] * config["channels"] * config["sample_bits"] // 8,
                    datetime.fromtimestamp(ended, UTC),
                    datetime.fromtimestamp(max(segment[2], row["verified_at"]), UTC),
                )
    finally:
        db.close()
    return {
        "schema": 2,
        "environment": manifest["environment"],
        "cutoff": cutoff.isoformat(),
        "inventory_sha256": checksum(manifest_path),
        "complete": True,
        "counts": counts,
        "eligible_pending_files": sum(c["eligible_pending_files"] for c in counts.values()),
        "receipt_mismatches": sum(c["receipt_mismatches"] for c in counts.values()),
        "unknown_eligible_files": sum(c["invalid_metadata"] for c in counts.values()),
        "remote_physical_readback": False,
        "deleted_files": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--quarantine", type=Path, required=True)
    args = parser.parse_args()
    exclusion = json.loads(args.quarantine.read_text())
    manifest = json.loads(args.manifest.read_text())
    if exclusion.get("schema") != 1 or exclusion.get("environment") != manifest.get("environment"):
        raise ArchiveBlocked("Verified quarantine register required")
    excluded = [(item["kind"], item["identity"]) for item in exclusion["excluded"]]
    result = reconcile(args.manifest, args.output, excluded=excluded)
    save_json(args.output / "reconciliation.json", result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
