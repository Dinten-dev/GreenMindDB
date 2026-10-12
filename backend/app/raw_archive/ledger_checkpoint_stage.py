"""Compress a source-authoritative checkpoint on the isolated archive host."""

import argparse
import json
import os
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import catalog_stages, wav_catalog
from .health import SafetyPause
from .policy import ArchiveBlocked, checksum


def convert(source, proof_path, output, *, checkpoint=lambda: None):
    for path in (source, proof_path):
        if (
            not path.is_absolute()
            or path.resolve() != path
            or not path.is_file()
            or path.stat().st_uid != os.getuid()
            or path.stat().st_mode & 0o077
        ):
            raise ArchiveBlocked("Private independent checkpoint required")
    if proof_path.stat().st_size > 16384 or source.stat().st_size > 512 * 1024**2:
        raise ArchiveBlocked("Bounded checkpoint required")
    proof = json.loads(proof_path.read_text())
    begin, end = (datetime.fromisoformat(proof[k]) for k in ("started_at", "finished_at"))
    now = datetime.now(UTC)
    if (
        proof.get("schema") != 1
        or proof.get("complete") is not True
        or proof.get("source_integrity_checked") is not False
        or proof.get("archive_integrity_required") is not True
        or proof.get("source_authority") != "Biolingo"
        or proof.get("environment") != "production"
        or proof.get("created_by_uid") != 996
        or begin.tzinfo is None
        or end.tzinfo is None
        or not 0 <= (end - begin).total_seconds() <= 120
        or not now - timedelta(hours=24) <= begin <= end <= now
        or type(proof.get("rows")) is not int
        or proof["rows"] < 0
        or proof.get("bytes") != source.stat().st_size
        or proof.get("sha256") != checksum(source)
    ):
        raise ArchiveBlocked("Source checkpoint provenance or integrity differs")
    checkpoint()
    # Reject corrupt source cuts before exporting or publishing any ledger stage.
    # All full-page validation stays off the production ingestion host.
    db = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=2)
    paused = None

    def progress():
        nonlocal paused
        try:
            checkpoint()
        except SafetyPause as error:
            paused = error
            return 1
        return 0

    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA cache_size=-2048")
        db.set_progress_handler(progress, 1000)
        try:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ArchiveBlocked("Source checkpoint integrity failed")
        except sqlite3.OperationalError:
            if paused is not None:
                raise paused from None
            raise
    finally:
        db.close()
    checkpoint()
    wav_catalog.private_directory(output)
    parts = wav_catalog.export_ledger(source, output, checkpoint, compresslevel=1)
    if sum(p["rows"] for p in parts) != proof["rows"]:
        raise ArchiveBlocked("Checkpoint row count differs")
    if sum(p["decoded_bytes"] for p in parts) > 512 * 1024**2:
        raise SafetyPause("ledger_spool_budget")
    checkpoint()
    stage = catalog_stages.completed_stage(
        "ledger",
        proof["started_at"],
        parts,
        finished_at=proof["finished_at"],
        processed_at=datetime.now(UTC).isoformat(),
        source_sha256=proof["sha256"],
        archive_integrity_checked=True,
    )
    catalog_stages.validate_stage(stage, "ledger", output)
    wav_catalog.save_json(output / "stage.json", stage)
    return {
        "status": "ledger_complete",
        "rows": proof["rows"],
        "complete_catalog": False,
        "deleted_files": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--proof", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from .archive_host_catalog import configured

    configured()
    started = time.monotonic()

    def check():
        if time.monotonic() - started >= 110:
            raise SafetyPause("ledger_processing_deadline")
        # No source SQL/lease is used during local processing. Local reserves remain.
        memory = (
            next(
                int(x.split()[1])
                for x in Path("/proc/meminfo").read_text().splitlines()
                if x.startswith("MemAvailable:")
            )
            / 1024
        )
        if memory < 800:
            raise SafetyPause("archive_memory")
        if os.getloadavg()[0] > 2:
            raise SafetyPause("archive_load")

    print(json.dumps(convert(args.source, args.proof, args.output, checkpoint=check)))


if __name__ == "__main__":
    try:
        main()
    except SafetyPause as error:
        print(json.dumps({"status": "paused", "reason": error.code, "deleted_files": 0}))
        raise SystemExit(75) from None
    except Exception as error:
        print(
            json.dumps(
                {"status": "blocked", "error_type": type(error).__name__, "deleted_files": 0}
            )
        )
        raise SystemExit(1) from None
