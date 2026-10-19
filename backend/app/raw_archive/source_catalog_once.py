"""One guarded WAV-only source export, with independent read-only recovery.

An optional preparation attempt on the existing source during actual headroom.
This never changes schedules or bypasses the pending archive-host access gate.
"""

import argparse
import json
import os
import pwd
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from . import wav_catalog as catalog
from .coordination import lease
from .health import SafetyPause
from .policy import ArchiveBlocked
from .read_only_box import configured_catalog_read_only_box
from .recovery import backup_ledger
from .runner import destination_from_environment, health_probe, metadata_source_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        os.uname().nodename != "Biolingo"
        or os.geteuid() != pwd.getpwnam("greenmind-raw-copy").pw_uid
        or os.environ.get("RAW_ARCHIVE_ENVIRONMENT") != "production"
        or os.environ.get("RAW_ARCHIVE_WORKER_ROLE") == "archive"
        or os.environ.get("RAW_ARCHIVE_COORDINATION_DIR")
        != "/var/lib/greenmind-archive-coordination"
    ):
        raise ArchiveBlocked("Only the explicit source preparation is permitted")
    if any(
        os.environ.get(name) != "false"
        for name in (
            "RAW_ARCHIVE_ENABLED",
            "RAW_ARCHIVE_DELETE_ENABLED",
            "RETENTION_ENABLED",
            "DIRECT_RETENTION_ENABLED",
            "VISUAL_PRUNE_ENABLED",
        )
    ):
        raise ArchiveBlocked("All destructive policies must stay disabled")
    if args.output.exists():
        raise ArchiveBlocked("Retain earlier evidence; use a new source attempt")
    os.environ["RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB"] = str(
        max(640, int(os.environ.get("RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB", "640")))
    )
    os.environ["RAW_ARCHIVE_MAX_HOST_LOAD"] = str(
        min(2.4, float(os.environ.get("RAW_ARCHIVE_MAX_HOST_LOAD", "2.4")))
    )
    probe = health_probe()
    reader = box = None
    start = time.monotonic()

    def checkpoint():
        if time.monotonic() - start >= 110:
            raise SafetyPause("catalog_phase_deadline")
        if not probe():
            raise SafetyPause(probe.last.get("reason", "headroom"), probe.last)

    try:
        with lease("catalog"):
            checkpoint()
            catalog.private_directory(args.output)
            began = datetime.now(UTC).isoformat()
            journal = args.output / "source-ledger.sqlite3"
            journal_proof = backup_ledger(
                Path("/var/lib/greenmind-raw-copy/archive.sqlite3"), journal, checkpoint=checkpoint
            )
            catalog.save_json(args.output / "journal-proof.json", journal_proof)
            manifest = {
                "schema": 2,
                "format": catalog.FORMAT,
                "allowlist_sha256": catalog.ALLOWLIST_SHA256,
                "environment": "production",
                "created_at": began,
                "direct_bucket": metadata_source_settings("direct", "production")["bucket"],
                "files": catalog.export_ledger(journal, args.output, checkpoint),
                "tables": {},
                "cross_database_atomic": False,
            }
            for kind in catalog.COLUMNS:
                settings = metadata_source_settings(kind, "production")
                url = make_url(settings["database"])
                if url.host != "127.0.0.1" or url.username != "greenmind_wav_metadata_b4ae52d674aa":
                    raise ArchiveBlocked("Only the dedicated narrow metadata reader is permitted")
                checkpoint()
                engine = create_engine(
                    settings["database"],
                    pool_size=1,
                    max_overflow=0,
                    connect_args={
                        "connect_timeout": 5,
                        "options": "-c default_transaction_read_only=on -c statement_timeout=8000 "
                        "-c lock_timeout=150 -c jit=off -c work_mem=2048",
                    },
                )
                try:
                    parts, counts = catalog.export_metadata(
                        engine, kind, args.output, checkpoint, fetch_rows=1000, compresslevel=1
                    )
                    manifest["files"].extend(parts)
                    manifest["tables"][kind] = counts
                    if (
                        sum(entry["bytes"] for entry in manifest["files"]) > 256 * 1024**2
                        or sum(entry["decoded_bytes"] for entry in manifest["files"])
                        > 512 * 1024**2
                    ):
                        raise SafetyPause("catalog_spool_budget")
                finally:
                    engine.dispose()
            checkpoint()
            catalog.manifest_check(manifest)
            catalog.save_json(args.output / "manifest.json", manifest)
        # Release source SQL and the lease before a distinct bounded publication.
        start = time.monotonic()
        with lease("catalog"):
            checkpoint()
            reader = configured_catalog_read_only_box()
            if reader is None:
                raise ArchiveBlocked("Separate read-only recovery identity required")
            box = destination_from_environment()
            if box.identity != reader.identity:
                raise ArchiveBlocked("Independent archive destination differs")
            with box.session(args.output):
                capacity = box.capacity()
                size = sum(entry["bytes"] for entry in manifest["files"])
                if size > 256 * 1024**2:
                    raise SafetyPause("catalog_spool_budget")
                required = max(2 * 1024**3, (capacity["total_bytes"] + 19) // 20) + size * 2
                if capacity["available_bytes"] < required:
                    raise SafetyPause("storage_box_space")
                reference = catalog.publish(args.output, manifest, box, "production", checkpoint)
                proof = catalog.restore_bundle(
                    reader,
                    reference,
                    args.output / "independent-readback",
                    "production",
                    checkpoint=checkpoint,
                )
                checkpoint()
                proof.update(
                    complete=True, independent_read_identity=True, deletion_readiness=False
                )
                catalog.save_json(args.output / "verified.json", proof)
                print(json.dumps(proof))
    finally:
        probe.close()
        if reader is not None:
            reader.close()
        if box is not None:
            box.close()


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
