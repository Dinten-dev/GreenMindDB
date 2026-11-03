"""Bounded native COPY boundary scan for the existing offline V3 part format."""

import hashlib
import json
import os
import stat
import sys
import time
from pathlib import Path

from .policy import ArchiveBlocked
from .scientific_capture import HEADER, ROW_BYTES, TRAILER
from .scientific_native import scanner_factory
from .scientific_parts import (
    MAX_MANIFEST,
    MAX_PART_BYTES,
    MAX_PART_ROWS,
    PART_FORMAT,
    SOURCE_SCOPE_SHA256,
    _part_entry,
    _private,
    _write_immutable,
    load_capture,
    need,
)

CHUNK_BYTES = 512 * 1024
PAGE_RELEASE_BYTES = 8 * 1024**2


def _release_processed_pages(fd, start, end):
    if not sys.platform.startswith("linux") or end <= start:
        return
    if not hasattr(os, "posix_fadvise") or not hasattr(os, "POSIX_FADV_DONTNEED"):
        raise ArchiveBlocked("Linux page-cache release unavailable")
    try:
        os.posix_fadvise(fd, start, end - start, os.POSIX_FADV_DONTNEED)
    except OSError:
        raise ArchiveBlocked("Linux page-cache release failed") from None


def fast_prepare_index(
    source,
    capture_sha256,
    output_file,
    *,
    checkpoint,
    library,
    library_sha256,
    target_part_bytes=MAX_PART_BYTES,
    parent_mode=False,
):
    """Index exact COPY bytes with bounded reads; no data spool or network access."""
    if not callable(checkpoint):
        raise ArchiveBlocked("Bounded offline checkpoint required")
    need(type(parent_mode) is bool, "Explicit parent native indexing mode required")
    if parent_mode:
        need(
            target_part_bytes <= 16 * 1024**2, "Parent native part budget exceeds existing workset"
        )
    if os.geteuid() != 0:
        raise ArchiveBlocked("Root-owned offline V3 index required")
    if (
        type(target_part_bytes) is not int
        or not len(HEADER) + len(TRAILER) <= target_part_bytes <= MAX_PART_BYTES
    ):
        raise ArchiveBlocked("Invalid V3 target part budget")
    checkpoint()
    last_checkpoint = time.monotonic()

    def bounded_checkpoint(*, force=False):
        nonlocal last_checkpoint
        if force or time.monotonic() - last_checkpoint >= 0.25:
            checkpoint()
            last_checkpoint = time.monotonic()

    source, output_file = Path(source), Path(output_file)
    need(
        source.is_absolute()
        and source.resolve(strict=False) == source
        and output_file.is_absolute()
        and output_file.resolve(strict=False) == output_file,
        "Canonical absolute V3 input and output required",
    )
    for ancestor in set(source.parents) | set(output_file.parent.parents):
        metadata = ancestor.lstat()
        need(
            stat.S_ISDIR(metadata.st_mode)
            and metadata.st_uid == 0
            and not metadata.st_mode & 0o022,
            "Root-protected V3 ancestry required",
        )
    need(not os.path.lexists(output_file), "New V3 part inventory required")
    parent = output_file.parent.lstat()
    need(
        stat.S_ISDIR(parent.st_mode)
        and parent.st_uid == os.geteuid()
        and stat.S_IMODE(parent.st_mode) == 0o700,
        "Private V3 index directory required",
    )
    scanner_class = scanner_factory(library, library_sha256)
    # File hashes are checked during the single streaming scan below.
    if parent_mode:
        from . import parent_capture

        proof = parent_capture.load_capture(
            source, capture_sha256, checkpoint=bounded_checkpoint, verify_files=False
        )
    else:
        proof = load_capture(
            source, capture_sha256, checkpoint=bounded_checkpoint, verify_files=False
        )
    parts = []
    for entry in proof["files"]:
        bounded_checkpoint(force=True)
        origin = source / entry["file"]
        before = _private(origin, entry["bytes"])
        fd = os.open(origin, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as body:
            opened = os.fstat(body.fileno())
            identity = (
                "st_dev",
                "st_ino",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
                "st_mode",
                "st_uid",
                "st_nlink",
            )
            need(
                all(getattr(opened, key) == getattr(before, key) for key in identity),
                "V3 input generation differs",
            )
            header = body.read(len(HEADER))
            need(header == HEADER, "V3 COPY header differs")
            whole = hashlib.sha256(header)
            scanner = scanner_class(len(entry["columns"]))
            scanner.feed(header)
            pending = b""
            file_offset = len(HEADER)
            released = 0
            previous_end = file_offset
            start = file_offset
            first = count = rows = payload = 0
            table_parts = []
            current = hashlib.sha256(HEADER)

            while True:
                bounded_checkpoint()
                block = body.read(min(CHUNK_BYTES, PAGE_RELEASE_BYTES - (file_offset - released)))
                if not block:
                    break
                whole.update(block)
                position = 0
                while position < len(block):
                    consumed, ends = scanner.scan_rows(block[position:], capacity=MAX_PART_ROWS)
                    need(0 < consumed <= len(block) - position, "Native scanner made no progress")
                    piece = block[position : position + consumed]
                    previous_pending = len(pending)
                    combined = pending + piece
                    hash_from = row_start = 0
                    for row_index, end in enumerate(ends):
                        if row_index % 2048 == 0:
                            bounded_checkpoint()
                        absolute_end = file_offset + position + end
                        row_bytes = absolute_end - previous_end
                        local_end = previous_pending + end
                        need(
                            0 < row_bytes <= ROW_BYTES and local_end <= len(combined),
                            "V3 native row boundary differs",
                        )
                        need(
                            len(HEADER) + row_bytes + len(TRAILER) <= target_part_bytes,
                            "Single V3 row exceeds part budget",
                        )
                        if rows and (
                            rows >= MAX_PART_ROWS
                            or len(HEADER) + payload + row_bytes + len(TRAILER) > target_part_bytes
                        ):
                            current.update(memoryview(combined)[hash_from:row_start])
                            table_parts.append(
                                _part_entry(
                                    entry["table"],
                                    len(table_parts),
                                    first,
                                    rows,
                                    start,
                                    previous_end,
                                    payload,
                                    current,
                                )
                            )
                            need(
                                len(parts) + len(table_parts) <= MAX_MANIFEST // 64,
                                "V3 part inventory exceeds budget",
                            )
                            start, first, payload, rows = previous_end, count, 0, 0
                            current = hashlib.sha256(HEADER)
                            hash_from = row_start
                        payload += row_bytes
                        rows += 1
                        count += 1
                        previous_end = absolute_end
                        row_start = local_end
                    if ends:
                        current.update(memoryview(combined)[hash_from:row_start])
                        pending = combined[row_start:]
                    else:
                        pending = combined
                    need(len(pending) <= ROW_BYTES + len(TRAILER), "V3 partial row exceeds budget")
                    position += consumed
                file_offset += len(block)
                if file_offset - released >= PAGE_RELEASE_BYTES:
                    _release_processed_pages(body.fileno(), released, file_offset)
                    released = file_offset
            need(scanner.finish() == count == entry["rows"], "V3 native row count differs")
            need(
                file_offset == entry["bytes"]
                and pending == TRAILER
                and previous_end == entry["bytes"] - len(TRAILER),
                "V3 COPY trailer differs",
            )
            table_parts.append(
                _part_entry(
                    entry["table"],
                    len(table_parts),
                    first,
                    rows,
                    start,
                    previous_end,
                    payload,
                    current,
                )
            )
            after = os.fstat(body.fileno())
            current_path = origin.lstat()
            need(
                all(
                    getattr(before, key) == getattr(after, key) == getattr(current_path, key)
                    for key in identity
                ),
                "V3 source changed during indexing",
            )
            need(whole.hexdigest() == entry["sha256"], "V3 native table hash differs")
            _release_processed_pages(body.fileno(), released, file_offset)
        parts.extend(table_parts)
    index = {
        "format": PART_FORMAT,
        "capture_id": proof["capture_id"],
        "capture_sha256": capture_sha256,
        "scope_sha256": SOURCE_SCOPE_SHA256,
        "complete": True,
        "max_part_bytes": MAX_PART_BYTES,
        "max_part_rows": MAX_PART_ROWS,
        "parts": parts,
        "parent_fk_closure_proven": False,
        "authenticated": False,
    }
    if parent_mode:
        from .parent_scope import SCOPE_SHA256 as PARENT_SCOPE_SHA256

        index = {
            "format": PART_FORMAT,
            "capture_sha256": capture_sha256,
            "scope_sha256": PARENT_SCOPE_SHA256,
            "parts": parts,
        }
        parent_capture.validate_index(proof, index, capture_sha256)
    raw = (json.dumps(index, sort_keys=True, separators=(",", ":")) + "\n").encode()
    need(len(raw) <= MAX_MANIFEST, "V3 part inventory exceeds budget")
    bounded_checkpoint(force=True)
    _write_immutable(output_file, raw)
    return {"index": index, "index_sha256": hashlib.sha256(raw).hexdigest()}
