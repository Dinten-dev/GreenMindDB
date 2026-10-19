"""Bounded offline phases from one completed, consistent source capture.

No source connections, writes or new source snapshots. Every table keeps the
original capture window/hash. Failed phase outputs remain private and intact.
"""

import argparse
import json
import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

from . import catalog_capture, catalog_stages, wav_catalog
from .health import SafetyPause
from .policy import ArchiveBlocked, checksum


def capture_info(source):
    path = source / "capture.json"
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_uid != os.getuid()
        or path.stat().st_mode & 0o077
        or path.stat().st_size > wav_catalog.ROW_BYTES
    ):
        raise ArchiveBlocked("Private completed capture required")
    proof = json.loads(path.read_text())
    kind = proof.get("kind")
    if (
        proof.get("schema") != 1
        or kind not in wav_catalog.COLUMNS
        or proof.get("format") not in {"wav_pgcopy_text_v1", "wav_pgcopy_native_v2"}
        or proof.get("complete") is not True
        or proof.get("source_read_only") is not True
        or proof.get("allowlist_sha256") != wav_catalog.ALLOWLIST_SHA256
    ):
        raise ArchiveBlocked("Only a complete WAV-only source snapshot is permitted")
    begin, end = (datetime.fromisoformat(proof[key]) for key in ("started_at", "finished_at"))
    if begin.tzinfo is None or end.tzinfo is None or not 0 <= (end - begin).total_seconds() <= 120:
        raise ArchiveBlocked("Original source phase exceeded its budget")
    entries = proof["files"]
    if (
        len(entries) != len(wav_catalog.COLUMNS[kind])
        or {e["table"] for e in entries} != set(wav_catalog.COLUMNS[kind])
        or any(
            type(e["bytes"]) is not int
            or e["bytes"] <= 0
            or type(e["rows"]) is not int
            or e["rows"] < 0
            for e in entries
        )
        or sum(e["bytes"] for e in entries) > catalog_capture.CAPTURE_BYTES
    ):
        raise ArchiveBlocked("Capture table coverage or byte bounds differ")
    return proof, checksum(path)


def process_table(source, table, output, checkpoint=lambda: None):
    proof, capture_sha = capture_info(source)
    kind = proof["kind"]
    if table not in wav_catalog.COLUMNS[kind] or output.exists():
        raise ArchiveBlocked("Exact table and new private phase output required")
    entry = next(e for e in proof["files"] if e["table"] == table)
    path = source / entry["file"]
    fields = wav_catalog.COLUMNS[kind][table]
    if (
        entry["file"] != table + ".pgcopy"
        or path.is_symlink()
        or path.stat().st_uid != os.getuid()
        or path.stat().st_mode & 0o077
        or path.stat().st_size != entry["bytes"]
        or checksum(path) != entry["sha256"]
        or len(entry["types"]) != len(fields)
        or any(t not in catalog_capture.TYPES for t in entry["types"])
    ):
        raise ArchiveBlocked("Source table changed or its columns differ")
    checkpoint()
    wav_catalog.private_directory(output)
    disk = shutil.disk_usage(output)
    if disk.free < max(2 * 1024**3, (disk.total + 19) // 20) + entry["bytes"] * 2:
        raise SafetyPause("offline_disk_reserve")
    parts = wav_catalog.Parts(output, kind, checkpoint, compresslevel=1)
    count = 0
    try:
        for values in catalog_capture.rows(
            path, entry["types"], native=proof["format"] == "wav_pgcopy_native_v2"
        ):
            parts.write(
                wav_catalog.validate_row(kind, table, dict(zip(fields, values, strict=True)))
            )
            count += 1
    finally:
        parts.finish()
    checkpoint()
    if count != entry["rows"]:
        raise ArchiveBlocked("Decoded source table count differs")
    result = {
        "schema": 1,
        "kind": kind,
        "table": table,
        "rows": count,
        "capture_sha256": capture_sha,
        "allowlist_sha256": wav_catalog.ALLOWLIST_SHA256,
        "complete": True,
        "scientific_validation_complete": True,
        "files": parts.files,
        "deleted_files": 0,
    }
    wav_catalog.save_json(output / "table.json", result)
    return result


def assemble(source, tables, output, checkpoint=lambda: None):
    proof, capture_sha = capture_info(source)
    kind = proof["kind"]
    if set(tables) != set(wav_catalog.COLUMNS[kind]) or output.exists():
        raise ArchiveBlocked("Every exact source table and new output are required")
    verified, counts = [], {}
    for entry in proof["files"]:
        checkpoint()
        folder = Path(tables[entry["table"]])
        meta = folder / "table.json"
        if (
            meta.is_symlink()
            or meta.stat().st_uid != os.getuid()
            or meta.stat().st_mode & 0o077
            or meta.stat().st_size > wav_catalog.ROW_BYTES
        ):
            raise ArchiveBlocked("Private table phase proof required")
        phase = json.loads(meta.read_text())
        if (
            phase.get("schema") != 1
            or phase.get("kind") != kind
            or phase.get("table") != entry["table"]
            or phase.get("rows") != entry["rows"]
            or phase.get("capture_sha256") != capture_sha
            or phase.get("allowlist_sha256") != wav_catalog.ALLOWLIST_SHA256
            or phase.get("complete") is not True
            or phase.get("scientific_validation_complete") is not True
        ):
            raise ArchiveBlocked("Phase does not cover the same consistent source capture")
        if sum(p["rows"] for p in phase["files"]) != entry["rows"]:
            raise ArchiveBlocked("Table phase omits rows")
        for part in phase["files"]:
            path = folder / part["file"]
            if (
                Path(part["file"]).name != part["file"]
                or part.get("kind") != kind
                or path.is_symlink()
                or not path.is_file()
                or path.stat().st_uid != os.getuid()
                or path.stat().st_mode & 0o077
                or path.stat().st_size != part["bytes"]
                or checksum(path) != part["sha256"]
                or not 0 < part["decoded_bytes"] <= wav_catalog.PART_BYTES
            ):
                raise ArchiveBlocked("Completed table part changed")
            verified.append((path, part))
        counts[entry["table"]] = entry["rows"]
    wav_catalog.private_directory(output)
    files = []
    for index, (source_part, part) in enumerate(verified):
        checkpoint()
        name = f"{kind}-{index:06d}.jsonl.gz"
        # Same private filesystem: retain original bytes, avoid another full spool.
        os.link(source_part, output / name)
        files.append(part | {"file": name})
    result = catalog_stages.completed_stage(
        kind,
        proof["started_at"],
        files,
        counts,
        finished_at=proof["finished_at"],
        processed_at=datetime.now(UTC).isoformat(),
        capture_sha256=capture_sha,
        **(
            {"direct_bucket": os.environ.get("RAW_ARCHIVE_DIRECT_S3_BUCKET", "")}
            if kind == "direct"
            else {}
        ),
    )
    catalog_stages.validate_stage(result, kind, output)
    wav_catalog.save_json(output / "stage.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--table", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    start = time.monotonic()

    def checkpoint():
        if time.monotonic() - start >= 110:
            raise SafetyPause("offline_phase_deadline")
        memory = int(
            next(
                line.split()[1]
                for line in Path("/proc/meminfo").read_text().splitlines()
                if line.startswith("MemAvailable:")
            )
        )
        if memory < 800 * 1024 or os.getloadavg()[0] > 2:
            raise SafetyPause("archive_headroom")

    try:
        checkpoint()
        result = process_table(args.source, args.table, args.output, checkpoint)
        print(
            json.dumps(
                {
                    "status": "table_validated",
                    "table": result["table"],
                    "rows": result["rows"],
                    "parts": len(result["files"]),
                    "deleted_files": 0,
                }
            )
        )
    except SafetyPause as error:
        print(json.dumps({"status": "paused", "reason": error.code, "deleted_files": 0}))
        return 75
    except Exception as error:  # noqa: BLE001 - preserve private data, emit only type
        print(
            json.dumps(
                {"status": "blocked", "error_type": type(error).__name__, "deleted_files": 0}
            )
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
