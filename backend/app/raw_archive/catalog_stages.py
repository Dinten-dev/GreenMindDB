"""Completed-source checkpoints: local work never consumes a production SQL lease."""

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from .policy import ArchiveBlocked, checksum
from .wav_catalog import (
    ALLOWLIST_SHA256,
    COLUMNS,
    FORMAT,
    ROW_BYTES,
    manifest_check,
    private_directory,
    restore_parts,
    save_json,
)

KINDS = ("ledger", "gateway", "direct")


def validate_stage(stage, kind, folder):
    if (
        stage.get("schema") != 1
        or stage.get("kind") != kind
        or stage.get("environment") != "production"
        or stage.get("allowlist_sha256") != ALLOWLIST_SHA256
        or stage.get("complete") is not True
    ):
        raise ArchiveBlocked("Only complete allowlisted source stages may be reused")
    try:
        times = [datetime.fromisoformat(stage[name]) for name in ("started_at", "finished_at")]
    except (KeyError, TypeError, ValueError) as error:
        raise ArchiveBlocked("Invalid source snapshot window") from error
    if any(value.tzinfo is None for value in times) or times[1] < times[0]:
        raise ArchiveBlocked("Invalid source snapshot window")
    if kind != "ledger" and (times[1] - times[0]).total_seconds() > 120:
        raise ArchiveBlocked("Source snapshot exceeded its two-minute budget")
    if kind != "ledger" and set(stage.get("tables", {})) != set(COLUMNS[kind]):
        raise ArchiveBlocked("Source stage omits required tables")
    if any(type(count) is not int or count < 0 for count in stage.get("tables", {}).values()):
        raise ArchiveBlocked("Invalid source table counts")
    files = stage.get("files", [])
    if not files or any(entry.get("kind") != kind for entry in files):
        raise ArchiveBlocked("Source stage is empty or crosses sources")
    seen = set()
    for entry in files:
        name = entry.get("file", "")
        if not re.fullmatch(kind + r"-\d{6}\.jsonl\.gz", name) or name in seen:
            raise ArchiveBlocked("Unsafe source checkpoint part")
        seen.add(name)
        path = folder / name
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != entry["bytes"]
            or checksum(path) != entry["sha256"]
        ):
            raise ArchiveBlocked("Completed source part changed")
    return stage


def load_stage(folder, kind):
    path = folder / "stage.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > ROW_BYTES:
        raise ArchiveBlocked("Private bounded source checkpoint required")
    if path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
        raise ArchiveBlocked("Source checkpoint is not operator-private")
    return validate_stage(json.loads(path.read_text()), kind, folder)


def assemble(stages, output):
    """Full local decoding/restore precedes a final manifest; no source connections."""
    if set(stages) != set(KINDS):
        raise ArchiveBlocked("Both complete metadata sources and a journal are required")
    private_directory(output)
    completed = {kind: load_stage(Path(stages[kind]), kind) for kind in KINDS}
    direct_bucket = completed["direct"].get("direct_bucket", "")
    if not direct_bucket.startswith("greenmind-direct-production"):
        raise ArchiveBlocked("Exact production Direct bucket is required")
    manifest = {
        "schema": 2,
        "format": FORMAT,
        "allowlist_sha256": ALLOWLIST_SHA256,
        "environment": "production",
        # Conservative cutoff: distinct source snapshots are not one atomic snapshot.
        "created_at": min(stage["started_at"] for stage in completed.values()),
        "direct_bucket": direct_bucket,
        "files": [],
        "tables": {kind: completed[kind]["tables"] for kind in COLUMNS},
        "source_windows": {
            kind: {name: stage[name] for name in ("started_at", "finished_at")}
            for kind, stage in completed.items()
        },
        "cross_database_atomic": False,
    }
    for kind in KINDS:
        for entry in completed[kind]["files"]:
            source = Path(stages[kind]) / entry["file"]
            target = output / entry["file"]
            # Same-volume immutable hardlinks; never overwrite or remove source evidence.
            os.link(source, target)
            manifest["files"].append(entry)
    manifest_check(manifest)
    restored = restore_parts(
        manifest,
        {entry["file"]: output / entry["file"] for entry in manifest["files"]},
        output / "local-restore.sqlite3",
    )
    save_json(output / "local-restore.json", {"restored": restored, "deleted_files": 0})
    save_json(output / "manifest.json", manifest)
    raw = (output / "manifest.json").read_bytes()
    return {"complete": True, "parts": len(restored), "sha256": hashlib.sha256(raw).hexdigest()}


def completed_stage(kind, started_at, files, tables=None, **extra):
    return {
        "schema": 1,
        "kind": kind,
        "environment": "production",
        "allowlist_sha256": ALLOWLIST_SHA256,
        "complete": True,
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "files": files,
        "tables": tables or {},
        **extra,
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger-stage", type=Path, required=True)
    parser.add_argument("--gateway-stage", type=Path, required=True)
    parser.add_argument("--direct-stage", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(assemble({kind: getattr(args, kind + "_stage") for kind in KINDS}, args.output))
    )


if __name__ == "__main__":
    main()
