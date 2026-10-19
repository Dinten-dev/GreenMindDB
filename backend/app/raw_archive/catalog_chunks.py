"""Bounded offline chunks preserve one original, complete SQL snapshot."""

import json
import os
import shutil
import struct
from pathlib import Path

from . import catalog_capture as capture
from . import catalog_offline as offline
from . import wav_catalog
from .policy import ArchiveBlocked, checksum

HEADER = capture.SIGNATURE + struct.pack("!ii", 0, 0)
TRAILER = struct.pack("!h", -1)
MAX_ROWS = 20000
MAX_BYTES = 32 * 1024**2


def prepare(source, table, output, checkpoint=lambda: None):
    proof, capture_sha = offline.capture_info(source)
    if table not in wav_catalog.COLUMNS[proof["kind"]] or output.exists():
        raise ArchiveBlocked("Exact source table and new chunk output required")
    entry = next(e for e in proof["files"] if e["table"] == table)
    origin = source / entry["file"]
    if (
        entry["file"] != table + ".pgcopy"
        or origin.is_symlink()
        or origin.stat().st_uid != os.getuid()
        or origin.stat().st_mode & 0o077
        or origin.stat().st_size != entry["bytes"]
        or checksum(origin) != entry["sha256"]
    ):
        raise ArchiveBlocked("Original complete table changed")
    usage = shutil.disk_usage(output.parent)
    if usage.free < max(2 * 1024**3, usage.total // 20) + entry["bytes"] * 2:
        raise ArchiveBlocked("Preserve offline disk reserve")
    checkpoint()
    wav_catalog.private_directory(output)
    chunks, count, body, name, first, start, rows, payload = [], 0, None, None, 0, len(HEADER), 0, 0

    def finish(end):
        nonlocal body
        if body is None:
            return
        body.write(TRAILER)
        body.flush()
        os.fsync(body.fileno())
        body.close()
        path = output / name
        chunks.append(
            {
                "file": name,
                "sha256": checksum(path),
                "bytes": path.stat().st_size,
                "first_row": first,
                "rows": rows,
                "start": start,
                "end": end,
            }
        )
        body = None

    with origin.open("rb") as src:
        if capture.read_exact(src, len(HEADER)) != HEADER:
            raise ArchiveBlocked("Invalid original COPY header")
        while True:
            checkpoint()
            row_start = src.tell()
            raw = capture.read_exact(src, 2)
            columns = struct.unpack("!h", raw)[0]
            if columns == -1:
                if src.read(1) or src.tell() != entry["bytes"]:
                    raise ArchiveBlocked("Trailing original bytes")
                if body is None and not chunks:
                    name = "chunk-000000.pgcopy"
                    body = (output / name).open("xb")
                    os.fchmod(body.fileno(), 0o600)
                    body.write(HEADER)
                finish(row_start)
                break
            if columns != len(entry["types"]):
                raise ArchiveBlocked("Original COPY columns differ")
            row = bytearray(raw)
            for _ in entry["types"]:
                length_raw = capture.read_exact(src, 4)
                length = struct.unpack("!i", length_raw)[0]
                if length < -1 or len(row) + max(length, 0) + 4 > wav_catalog.ROW_BYTES:
                    raise ArchiveBlocked("Invalid bounded original row")
                row.extend(length_raw)
                if length >= 0:
                    row.extend(capture.read_exact(src, length))
            if body is not None and (rows >= MAX_ROWS or payload + len(row) > MAX_BYTES):
                finish(row_start)
            if body is None:
                name = f"chunk-{len(chunks):06d}.pgcopy"
                body = (output / name).open("xb")
                os.fchmod(body.fileno(), 0o600)
                body.write(HEADER)
                first, start, rows, payload = count, row_start, 0, 0
            body.write(row)
            rows += 1
            count += 1
            payload += len(row)
        if body is not None:
            body.close()
    if count != entry["rows"]:
        raise ArchiveBlocked("Original chunk row coverage differs")
    result = {
        "schema": 1,
        "complete": True,
        "kind": proof["kind"],
        "table": table,
        "capture_sha256": capture_sha,
        "source_table_sha256": entry["sha256"],
        "rows": count,
        "source_bytes": entry["bytes"],
        "types": entry["types"],
        "native": proof["format"] == "wav_pgcopy_native_v2",
        "chunks": chunks,
    }
    wav_catalog.save_json(output / "chunks.json", result)
    return result


def info(source, chunks):
    proof, capture_sha = offline.capture_info(source)
    meta = chunks / "chunks.json"
    if (
        meta.is_symlink()
        or meta.stat().st_size > wav_catalog.ROW_BYTES
        or meta.stat().st_mode & 0o077
    ):
        raise ArchiveBlocked("Private bounded chunk proof required")
    value = json.loads(meta.read_text())
    table = value.get("table")
    entry = next((e for e in proof["files"] if e["table"] == table), None)
    if (
        value.get("schema") != 1
        or value.get("complete") is not True
        or entry is None
        or value.get("capture_sha256") != capture_sha
        or value.get("source_table_sha256") != entry["sha256"]
        or value.get("types") != entry["types"]
        or value.get("rows") != entry["rows"]
        or value.get("source_bytes") != entry["bytes"]
        or value.get("native") != (proof["format"] == "wav_pgcopy_native_v2")
    ):
        raise ArchiveBlocked("Chunk proof differs from original snapshot")
    first, offset = 0, len(HEADER)
    for index, item in enumerate(value["chunks"]):
        if (
            item["file"] != f"chunk-{index:06d}.pgcopy"
            or item["first_row"] != first
            or item["start"] != offset
            or not 0 <= item["rows"] <= MAX_ROWS
            or item["end"] < offset
            or item["bytes"] != item["end"] - offset + len(HEADER) + 2
            or item["bytes"] > MAX_BYTES + len(HEADER) + 2
        ):
            raise ArchiveBlocked("Chunk coverage gap, overlap or bound differs")
        first += item["rows"]
        offset = item["end"]
    if not value["chunks"] or first != entry["rows"] or offset != entry["bytes"] - 2:
        raise ArchiveBlocked("Incomplete source row/byte coverage")
    return proof, value, checksum(meta)


def process(source, chunks, position, output, checkpoint=lambda: None):
    _, meta, meta_sha = info(source, chunks)
    if type(position) is not int or not 0 <= position < len(meta["chunks"]) or output.exists():
        raise ArchiveBlocked("Exact chunk and new output required")
    part = meta["chunks"][position]
    path = chunks / part["file"]
    if (
        path.is_symlink()
        or path.stat().st_mode & 0o077
        or path.stat().st_uid != os.getuid()
        or path.stat().st_size != part["bytes"]
        or checksum(path) != part["sha256"]
    ):
        raise ArchiveBlocked("Original framed chunk changed")
    checkpoint()
    wav_catalog.private_directory(output)
    parts = wav_catalog.Parts(output, meta["kind"], checkpoint, compresslevel=1)
    count = 0
    try:
        fields = wav_catalog.COLUMNS[meta["kind"]][meta["table"]]
        for values in capture.rows(path, meta["types"], native=meta["native"]):
            parts.write(
                wav_catalog.validate_row(
                    meta["kind"], meta["table"], dict(zip(fields, values, strict=True))
                )
            )
            count += 1
    finally:
        parts.finish()
    checkpoint()
    if count != part["rows"]:
        raise ArchiveBlocked("Validated chunk row count differs")
    result = {
        "schema": 1,
        "complete": True,
        "position": position,
        "rows": count,
        "chunks_sha256": meta_sha,
        "files": parts.files,
    }
    wav_catalog.save_json(output / "validated.json", result)
    return result


def assemble(source, chunks, phases, output, checkpoint=lambda: None):
    _, meta, meta_sha = info(source, chunks)
    if len(phases) != len(meta["chunks"]) or output.exists():
        raise ArchiveBlocked("Every exact validated chunk is required")
    verified = []
    for position, folder in enumerate(phases):
        checkpoint()
        phase = folder / "validated.json"
        if phase.is_symlink() or phase.stat().st_uid != os.getuid() or phase.stat().st_mode & 0o077:
            raise ArchiveBlocked("Private completed chunk proof required")
        value = json.loads(phase.read_text())
        if (
            value.get("complete") is not True
            or value.get("position") != position
            or value.get("chunks_sha256") != meta_sha
            or value.get("rows") != meta["chunks"][position]["rows"]
            or sum(p["rows"] for p in value["files"]) != value["rows"]
        ):
            raise ArchiveBlocked("Mixed or incomplete chunk validation")
        for item in value["files"]:
            path = folder / item["file"]
            if (
                Path(item["file"]).name != item["file"]
                or path.is_symlink()
                or path.stat().st_size != item["bytes"]
                or checksum(path) != item["sha256"]
            ):
                raise ArchiveBlocked("Validated output part changed")
            verified.append((path, item))
    wav_catalog.private_directory(output)
    files = []
    for position, (origin, part) in enumerate(verified):
        checkpoint()
        name = f"{meta['kind']}-{position:06d}.jsonl.gz"
        os.link(origin, output / name)
        files.append(part | {"file": name})
    result = {
        "schema": 1,
        "kind": meta["kind"],
        "table": meta["table"],
        "rows": meta["rows"],
        "capture_sha256": meta["capture_sha256"],
        "allowlist_sha256": wav_catalog.ALLOWLIST_SHA256,
        "complete": True,
        "scientific_validation_complete": True,
        "files": files,
        "deleted_files": 0,
    }
    wav_catalog.save_json(output / "table.json", result)
    return result
