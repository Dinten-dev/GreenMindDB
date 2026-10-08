"""Append-only scientific feature backup, independent of deletion readiness.

Source PostgreSQL remains authoritative. No RAW, accounts, secrets or deletes.
"""

import argparse
import gzip
import json
import os
import re
import shutil
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from . import wav_catalog as catalog
from .coordination import lease
from .health import SafetyPause
from .policy import ArchiveBlocked, checksum

FORMAT = "wav_features_v1"
TABLES = {
    "gateway": ("wav_feature", "wav_feature_version"),
    "direct": ("direct_segment", "direct_revision"),
}
MAX_BYTES = 256 * 1024**2


def check_manifest(manifest):
    if (
        manifest.get("format") != FORMAT
        or manifest.get("schema") != 1
        or manifest.get("allowlist_sha256") != catalog.ALLOWLIST_SHA256
        or manifest.get("environment") != "production"
        or set(manifest.get("tables", {})) != set(TABLES)
    ):
        raise ArchiveBlocked("Explicit feature-only manifest required")
    for kind, names in TABLES.items():
        counts = manifest["tables"][kind]
        if set(counts) != set(names) or any(type(n) is not int or n < 0 for n in counts.values()):
            raise ArchiveBlocked("Incomplete feature table counts")
    entries = manifest.get("files")
    if not isinstance(entries, list) or len(entries) > 256:
        raise ArchiveBlocked("Invalid feature parts")
    seen = set()
    for entry in entries:
        if (
            entry.get("kind") not in TABLES
            or not re.fullmatch(r"(?:gateway|direct)-[0-9]{6}\.jsonl\.gz", entry.get("file", ""))
            or entry["file"] in seen
            or not re.fullmatch(r"[a-f0-9]{64}", entry.get("sha256", ""))
            or type(entry.get("bytes")) is not int
            or not 0 < entry["bytes"] <= catalog.PART_BYTES + catalog.ROW_BYTES
            or type(entry.get("decoded_bytes")) is not int
            or not 0 < entry["decoded_bytes"] <= catalog.PART_BYTES
            or type(entry.get("rows")) is not int
            or entry["rows"] <= 0
        ):
            raise ArchiveBlocked("Unsafe feature part")
        seen.add(entry["file"])
    if sum(entry["bytes"] for entry in entries) > MAX_BYTES:
        raise ArchiveBlocked("Feature backup exceeds bounded spool")
    if sum(entry["decoded_bytes"] for entry in entries) > 512 * 1024**2:
        raise ArchiveBlocked("Feature decoding exceeds bounded spool")
    for kind in TABLES:
        if sum(e["rows"] for e in entries if e["kind"] == kind) != sum(
            manifest["tables"][kind].values()
        ):
            raise ArchiveBlocked("Feature part counts differ")


def restore(manifest, files, destination, checkpoint=lambda: None):
    """Every compressed row is decoded into a separate empty archive database."""
    check_manifest(manifest)
    if not destination.is_absolute() or destination.exists() or destination.is_symlink():
        raise ArchiveBlocked("Feature restore requires a new isolated database")
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    counts = {kind: dict.fromkeys(names, 0) for kind, names in TABLES.items()}
    with closing(sqlite3.connect(destination)) as db, db:
        db.execute("PRAGMA cache_size=-2048")
        db.execute(
            "CREATE TABLE records(kind TEXT, table_name TEXT, ordinal INTEGER, body TEXT, PRIMARY KEY(kind,table_name,ordinal))"
        )
        for entry in manifest["files"]:
            checkpoint()
            path = files[entry["file"]]
            if (
                path.is_symlink()
                or path.stat().st_size != entry["bytes"]
                or checksum(path) != entry["sha256"]
            ):
                raise ArchiveBlocked("Feature readback checksum mismatch")
            rows = decoded = 0
            with gzip.open(path, "rb") as body:
                while line := body.readline(catalog.ROW_BYTES + 2):
                    decoded += len(line)
                    if len(line) > catalog.ROW_BYTES + 1 or decoded > entry["decoded_bytes"]:
                        raise ArchiveBlocked("Feature decoding exceeds budget")
                    value = json.loads(line)
                    catalog.exact_keys(value, {"table", "row"}, "feature row", complete=True)
                    kind, table = entry["kind"], value["table"]
                    if table not in TABLES[kind]:
                        raise ArchiveBlocked("Unapproved feature table")
                    catalog.validate_row(kind, table, value["row"])
                    counts[kind][table] += 1
                    db.execute(
                        "INSERT INTO records VALUES (?,?,?,?)",
                        (kind, table, counts[kind][table], catalog.raw_json(value["row"]).decode()),
                    )
                    rows += 1
                    if rows % 100 == 0:
                        checkpoint()
            if rows != entry["rows"] or decoded != entry["decoded_bytes"]:
                raise ArchiveBlocked("Feature readback length mismatch")
        if counts != manifest["tables"]:
            raise ArchiveBlocked("Feature restoration counts differ")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ArchiveBlocked("Feature restoration integrity failure")
    return counts


def publish_and_restore(output, manifest, box, checkpoint=lambda: None):
    check_manifest(manifest)
    checkpoint()
    capacity = box.capacity()
    size = sum(entry["bytes"] for entry in manifest["files"])
    # Leave both 5% and 2 GiB, plus twice this upload for concurrent allocation.
    required = max(2 * 1024**3, (capacity["total_bytes"] + 19) // 20) + size * 2
    if capacity["available_bytes"] < required:
        raise SafetyPause("storage_box_space", {"required_bytes": required, **capacity})
    remote = dict(manifest, destination=box.identity, files=[])
    for entry in manifest["files"]:
        checkpoint()
        path = output / entry["file"]
        if (
            path.is_symlink()
            or checksum(path) != entry["sha256"]
            or path.stat().st_size != entry["bytes"]
        ):
            raise ArchiveBlocked("Local features changed before publication")
        key = f"production/catalog-backup/{entry['sha256']}.jsonl.gz"
        box.publish_catalog(path, key)
        remote["files"].append(dict(entry, remote_key=key))
    path = output / "offsite-features.json"
    catalog.save_json(path, remote)
    reference = {
        "key": f"production/catalog-backup/{checksum(path)}.json",
        "sha256": checksum(path),
        "size": path.stat().st_size,
    }
    checkpoint()
    box.publish_catalog(path, reference["key"])
    readback = output / "readback"
    catalog.private_directory(readback)
    returned = readback / "manifest.json"
    box.download_catalog(reference["key"], returned, reference["size"])
    if returned.stat().st_size != reference["size"] or checksum(returned) != reference["sha256"]:
        raise ArchiveBlocked("Feature manifest readback mismatch")
    independent = json.loads(returned.read_text())
    check_manifest(independent)
    files = {}
    for entry in independent["files"]:
        checkpoint()
        expected = f"production/catalog-backup/{entry['sha256']}.jsonl.gz"
        if entry.get("remote_key") != expected:
            raise ArchiveBlocked("Unexpected remote feature part")
        local = readback / entry["file"]
        box.download_catalog(expected, local, entry["bytes"])
        files[entry["file"]] = local
    counts = restore(independent, files, readback / "features.sqlite3", checkpoint)
    proof = {
        "format": FORMAT,
        "status": "verified",
        "created_at": datetime.now(UTC).isoformat(),
        "reference": reference,
        "tables": counts,
        "compressed_bytes": size,
        "storage_box": capacity,
        "deleted_files": 0,
        "deletion_readiness": False,
    }
    catalog.save_json(output / "verified.json", proof)
    return proof


def main():
    from sqlalchemy import create_engine

    from .runner import destination_from_environment, health_probe, metadata_source_settings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("RAW_ARCHIVE_ENVIRONMENT") != "production":
        raise ArchiveBlocked("Production feature backup requires explicit environment")
    if not os.environ.get("RAW_ARCHIVE_COORDINATION_DIR"):
        raise ArchiveBlocked("Source archive coordination must be configured")
    # Protect intake regardless of accidentally weaker supplied settings.
    os.environ["RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB"] = str(
        max(640, int(os.environ.get("RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB", "640")))
    )
    os.environ["RAW_ARCHIVE_MAX_HOST_LOAD"] = str(
        min(2.4, float(os.environ.get("RAW_ARCHIVE_MAX_HOST_LOAD", "2.4")))
    )
    probe = health_probe()
    start = time.monotonic()

    def checkpoint():
        if time.monotonic() - start > 120:
            raise SafetyPause("feature_runtime_budget")
        if not probe():
            raise SafetyPause(probe.last.get("reason", "headroom"), probe.last)
        if (
            args.output.exists()
            and sum(path.stat().st_size for path in args.output.glob("*.jsonl.gz")) > MAX_BYTES
        ):
            raise SafetyPause("feature_spool_budget")
        if args.output.exists() and shutil.disk_usage(args.output).free < 2 * 1024**3:
            raise SafetyPause("feature_local_space")

    try:
        with lease("catalog"):
            checkpoint()
            catalog.private_directory(args.output)
            if shutil.disk_usage(args.output).free < MAX_BYTES * 3:
                raise SafetyPause("feature_local_space")
            manifest = {
                "schema": 1,
                "format": FORMAT,
                "environment": "production",
                "allowlist_sha256": catalog.ALLOWLIST_SHA256,
                "created_at": datetime.now(UTC).isoformat(),
                "tables": {},
                "files": [],
            }
            for kind, tables in TABLES.items():
                engine = create_engine(
                    metadata_source_settings(kind, "production")["database"],
                    pool_size=1,
                    max_overflow=0,
                    connect_args={
                        "connect_timeout": 5,
                        "options": "-c default_transaction_read_only=on -c statement_timeout=8000 -c lock_timeout=150 -c jit=off -c work_mem=2048",
                    },
                )
                try:
                    parts, counts = catalog.export_metadata(
                        engine,
                        kind,
                        args.output,
                        checkpoint,
                        tables=tables,
                        fetch_rows=100,
                        compresslevel=1,
                    )
                    manifest["files"].extend(parts)
                    manifest["tables"][kind] = counts
                finally:
                    engine.dispose()
            check_manifest(manifest)
            catalog.save_json(args.output / "features.json", manifest)
        # SQL connections and their bounded source lease are closed before transfer.
        # Publication/restore gets a separate two-minute, equally guarded phase.
        start = time.monotonic()
        with lease("catalog"):
            checkpoint()
            box = destination_from_environment()
            try:
                with box.session(args.output):
                    proof = publish_and_restore(args.output, manifest, box, checkpoint)
            finally:
                box.close()
            print(json.dumps(proof))
    finally:
        probe.close()


def entrypoint():
    try:
        main()
        return 0
    except SafetyPause as error:
        print(json.dumps({"status": "paused", "reason": error.code, "deleted_files": 0}))
        return 75
    except Exception as error:
        # Database/SFTP exceptions may contain private configuration.
        print(
            json.dumps(
                {"status": "blocked", "error_type": type(error).__name__, "deleted_files": 0}
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(entrypoint())
