"""Archive-host WAV-only stages and independent catalog publication/restoration.

No application accounts, S3 writes, source deletion or retention activation.
SQL uses the same source-authoritative bounded lease as feature backup.
"""

import argparse
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from . import catalog_stages, wav_catalog
from .health import SafetyPause
from .policy import ArchiveBlocked
from .remote_lease import catalog_lease
from .runner import destination_from_environment, health_probe, metadata_source_settings


def configured():
    if (
        os.environ.get("RAW_ARCHIVE_WORKER_ROLE") != "archive"
        or os.environ.get("RAW_ARCHIVE_ENVIRONMENT") != "production"
        or os.environ.get("RAW_ARCHIVE_LEASE_HOST") != "188.245.247.156"
    ):
        raise ArchiveBlocked("Explicit private archive host and source authority required")
    for name in ("RAW_ARCHIVE_ENABLED", "RAW_ARCHIVE_DELETE_ENABLED"):
        if os.environ.get(name) != "false":
            raise ArchiveBlocked("Catalog preparation cannot enable deletion")
    os.environ["RAW_ARCHIVE_SOURCE_MIN_MEMORY_MIB"] = str(
        max(640, int(os.environ.get("RAW_ARCHIVE_SOURCE_MIN_MEMORY_MIB", "640")))
    )
    os.environ["RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB"] = str(
        max(800, int(os.environ.get("RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB", "800")))
    )
    os.environ["RAW_ARCHIVE_SOURCE_MAX_LOAD"] = str(
        min(2.4, float(os.environ.get("RAW_ARCHIVE_SOURCE_MAX_LOAD", "2")))
    )


def guarded(probe, started, check_lease=lambda: None):
    check_lease()
    if time.monotonic() - started >= 110:
        raise SafetyPause("catalog_phase_deadline")
    if not probe():
        raise SafetyPause(probe.last.get("reason", "headroom"), probe.last)


def export_source(kind, output, *, capture_only=False):
    configured()
    if kind not in ("gateway", "direct"):
        raise ArchiveBlocked("Exactly one metadata source is required")
    if not capture_only and (output / "stage.json").exists():
        catalog_stages.load_stage(output, kind)
        return {"status": "reused", "kind": kind, "deleted_files": 0}
    if output.exists():
        raise ArchiveBlocked("Retain partial source evidence; use a new stage directory")
    settings = metadata_source_settings(kind, "production")
    url = make_url(settings["database"])
    if url.host != "127.0.0.1" or url.username != "greenmind_wav_metadata_b4ae52d674aa":
        raise ArchiveBlocked("Only the configured narrow reader through loopback is permitted")
    probe = health_probe()
    started = time.monotonic()
    started_at = datetime.now(UTC).isoformat()
    try:
        with catalog_lease() as check_lease:

            def checkpoint():
                guarded(probe, started, check_lease)

            # Memory/route/lease failures open no SQL connection or output folder.
            checkpoint()
            if not capture_only:
                wav_catalog.private_directory(output)
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
                if capture_only:
                    from .catalog_capture import capture

                    proof = capture(engine, kind, output, checkpoint)
                    return {
                        "status": "captured",
                        "kind": kind,
                        "tables": {entry["table"]: entry["rows"] for entry in proof["files"]},
                        "decoded_validation_complete": False,
                        "deleted_files": 0,
                    }
                parts, tables = wav_catalog.export_metadata(
                    engine, kind, output, checkpoint, fetch_rows=1000, compresslevel=1
                )
            finally:
                engine.dispose()
            checkpoint()
            stage = catalog_stages.completed_stage(
                kind, started_at, parts, tables, direct_bucket=settings["bucket"]
            )
            catalog_stages.validate_stage(stage, kind, output)
            wav_catalog.save_json(output / "stage.json", stage)
            return {"status": "complete", "kind": kind, "tables": tables, "deleted_files": 0}
    finally:
        probe.close()


def publish_restore(manifest_path, output):
    from .read_only_box import configured_catalog_read_only_box

    configured()
    if (
        manifest_path.is_symlink()
        or manifest_path.stat().st_size > wav_catalog.ROW_BYTES
        or manifest_path.stat().st_mode & 0o077
        or manifest_path.stat().st_uid != os.getuid()
        or output.exists()
    ):
        raise ArchiveBlocked("Private completed manifest and new verification directory required")
    manifest = json.loads(manifest_path.read_text())
    wav_catalog.manifest_check(manifest)
    reader = configured_catalog_read_only_box()
    if reader is None:
        raise ArchiveBlocked("Separate configured read-only catalog access required")
    probe = health_probe()
    box = None
    try:
        started = time.monotonic()
        with catalog_lease() as check_lease:

            def checkpoint():
                guarded(probe, started, check_lease)

            checkpoint()
            box = destination_from_environment()
            if reader.identity != box.identity:
                raise ArchiveBlocked("Independent reader targets a different archive")
            with box.session(manifest_path.parent):
                capacity = box.capacity()
                incoming = sum(entry["bytes"] for entry in manifest["files"])
                if incoming > 256 * 1024**2:
                    raise SafetyPause("catalog_spool_budget")
                required = max(2 * 1024**3, (capacity["total_bytes"] + 19) // 20) + incoming * 2
                if capacity["available_bytes"] < required:
                    raise SafetyPause("storage_box_space")
                reference = wav_catalog.publish(
                    manifest_path.parent, manifest, box, "production", checkpoint
                )
                # Source SQL is already closed; recovery reads through sub1.
                proof = wav_catalog.restore_bundle(
                    reader, reference, output, "production", checkpoint=checkpoint
                )
                checkpoint()
                proof.update(
                    complete=True,
                    independent_read_identity=True,
                    deletion_readiness=False,
                    deleted_files=0,
                )
                wav_catalog.save_json(output / "verified.json", proof)
                return proof
    finally:
        probe.close()
        reader.close()
        if box is not None:
            box.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--kind",
        choices=("gateway", "direct", "publish", "capture-gateway", "capture-direct"),
        required=True,
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    if args.kind == "publish":
        if args.manifest is None:
            parser.error("A fully assembled WAV-only manifest is required")
        value = publish_restore(args.manifest, args.output)
    else:
        value = export_source(
            args.kind.removeprefix("capture-"),
            args.output,
            capture_only=args.kind.startswith("capture-"),
        )
    print(json.dumps(value))


if __name__ == "__main__":
    try:
        main()
    except SafetyPause as error:
        print(json.dumps({"status": "paused", "reason": error.code, "deleted_files": 0}))
        raise SystemExit(75) from None
    except Exception as error:
        # Never display driver errors that can contain configured credentials.
        print(
            json.dumps(
                {"status": "blocked", "error_type": type(error).__name__, "deleted_files": 0}
            )
        )
        raise SystemExit(1) from None
