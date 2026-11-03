"""Offline V3 native COPY part inventory and one-part materialization.

The whole capture remains on Source. This module makes no network connection,
reads no encryption key, publishes nothing and does not claim a restore.
"""

import hashlib
import json
import os
import re
import shutil
import stat
import struct
import sys
from datetime import datetime
from pathlib import Path
from typing import Protocol

from . import catalog_chunks
from .policy import ArchiveBlocked
from .scientific_capture import (
    CAPTURE_BYTES,
    FORMAT,
    HEADER,
    ROW_BYTES,
    TABLES,
    TRAILER,
    FrameCounter,
    digest_json,
    roster,
)
from .scientific_scope_v3 import SOURCE_SCOPE, SOURCE_SCOPE_SHA256

PART_FORMAT = "greenmind.science-native-parts.v3"
MAX_PART_BYTES = 32 * 1024**2
MAX_PART_ROWS = catalog_chunks.MAX_ROWS
MAX_MANIFEST = 4 * 1024**2
MAX_ARCHIVE_WORKSET = 256 * 1024**2


def need(value, reason):
    if not value:
        raise ArchiveBlocked(reason)


def _private(path, size=None):
    info = path.lstat()
    need(
        stat.S_ISREG(info.st_mode)
        and info.st_uid == os.geteuid()
        and info.st_nlink == 1
        and stat.S_IMODE(info.st_mode) == 0o400
        and (size is None or info.st_size == size),
        "Immutable private V3 input required",
    )
    return info


def _bytes(path, limit):
    before = _private(path)
    need(before.st_size <= limit, "V3 manifest exceeds bounded size")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as f:
        actual = os.fstat(f.fileno())
        need(
            (actual.st_dev, actual.st_ino) == (before.st_dev, before.st_ino),
            "V3 input generation differs",
        )
        data = f.read(limit + 1)
        after = os.fstat(f.fileno())
    need(
        len(data) == before.st_size
        and (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        == (after.st_size, after.st_mtime_ns, after.st_ctime_ns),
        "V3 input changed",
    )
    return data


def _hash(path, checkpoint, *, cooperative_pause=0.0):
    import math
    import time

    need(
        type(cooperative_pause) in (int, float)
        and math.isfinite(cooperative_pause)
        and 0 <= cooperative_pause <= 0.25,
        "Unsafe V3 cooperative hash pause",
    )
    before = _private(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as f:
        info = os.fstat(f.fileno())
        need(
            (info.st_dev, info.st_ino) == (before.st_dev, before.st_ino),
            "V3 source generation differs",
        )
        h = hashlib.sha256()
        advised = 0
        consumed = 0
        if sys.platform.startswith("linux"):
            need(
                hasattr(os, "posix_fadvise") and hasattr(os, "POSIX_FADV_DONTNEED"),
                "Linux source cache release unavailable",
            )
        while True:
            checkpoint()
            block = f.read(65536)
            if not block:
                break
            h.update(block)
            consumed += len(block)
            if sys.platform.startswith("linux") and consumed - advised >= 8 * 1024**2:
                try:
                    os.posix_fadvise(
                        f.fileno(), advised, consumed - advised, os.POSIX_FADV_DONTNEED
                    )
                except OSError as exc:
                    raise ArchiveBlocked("Linux source cache release failed") from exc
                advised = consumed
                if cooperative_pause:
                    time.sleep(cooperative_pause)
                    checkpoint()
        if sys.platform.startswith("linux") and consumed > advised:
            try:
                os.posix_fadvise(f.fileno(), advised, consumed - advised, os.POSIX_FADV_DONTNEED)
            except OSError as exc:
                raise ArchiveBlocked("Linux source cache release failed") from exc
        after = os.fstat(f.fileno())
    need(
        (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        == (after.st_size, after.st_mtime_ns, after.st_ctime_ns),
        "V3 source changed",
    )
    return h.hexdigest()


def load_capture(source, expected_sha256, *, checkpoint, verify_files=True, cooperative_pause=0.0):
    source = Path(source)
    d = source.lstat()
    need(
        stat.S_ISDIR(d.st_mode) and d.st_uid == os.geteuid() and stat.S_IMODE(d.st_mode) == 0o700,
        "Private complete capture directory required",
    )
    need(
        isinstance(expected_sha256, str) and re.fullmatch("[a-f0-9]{64}", expected_sha256),
        "Pinned V3 capture digest required",
    )
    raw = _bytes(source / "capture.json", MAX_MANIFEST)
    need(hashlib.sha256(raw).hexdigest() == expected_sha256, "V3 capture digest differs")
    proof = json.loads(raw)
    keys = {
        "format",
        "capture_id",
        "source",
        "complete",
        "source_read_only",
        "started_at",
        "finished_at",
        "source_identity",
        "snapshot_sha256",
        "scope_sha256",
        "scope",
        "files",
        "total_bytes",
        "cross_database_atomic",
        "global_parent_fk_closure_proven",
    }
    need(
        type(proof) is dict
        and set(proof) == keys
        and proof["format"] == FORMAT
        and proof["source"] == "gateway"
        and proof["complete"] is True
        and proof["source_read_only"] is True
        and proof["cross_database_atomic"] is False
        and proof["global_parent_fk_closure_proven"] is False,
        "Complete V3 source capture required",
    )
    need(
        type(proof["capture_id"]) is str
        and re.fullmatch("[a-f0-9]{32}", proof["capture_id"])
        and type(proof["snapshot_sha256"]) is str
        and re.fullmatch("[a-f0-9]{64}", proof["snapshot_sha256"]),
        "V3 source capture identity required",
    )
    need(
        proof["scope"] == SOURCE_SCOPE
        and proof["scope_sha256"] == SOURCE_SCOPE_SHA256
        and digest_json(proof["scope"]) == SOURCE_SCOPE_SHA256,
        "Full V3 source catalog differs",
    )
    ident = proof["source_identity"]
    need(
        type(ident) is dict
        and set(ident)
        == {
            "database",
            "database_oid",
            "role",
            "session_role",
            "role_oid",
            "session_oid",
            "role_unprivileged",
            "server_version_num",
            "transaction_read_only",
            "in_recovery",
            "server_address",
            "server_port",
            "postmaster_start",
        }
        and ident.get("server_address") == "172.28.20.5/32"
        and ident.get("database") == "plantdb"
        and ident.get("database_oid") == 16384
        and ident.get("role") == "greenmind_archive_reader_33fe395"
        and ident.get("session_role") == ident.get("role")
        and ident.get("role_oid") == ident.get("session_oid") == 499352
        and ident.get("role_unprivileged") is True
        and ident.get("server_version_num") == 150010
        and ident.get("transaction_read_only") == "on"
        and ident.get("in_recovery") is False
        and ident.get("server_port") == 5432
        and ident.get("postmaster_start") == SOURCE_SCOPE["postmaster_start"],
        "V3 source identity differs",
    )
    started = datetime.fromisoformat(proof["started_at"])
    finished = datetime.fromisoformat(proof["finished_at"])
    need(
        started.tzinfo is not None
        and finished.tzinfo is not None
        and 0 <= (finished - started).total_seconds() <= 110,
        "V3 capture window differs",
    )
    columns = roster()
    need(
        type(proof["files"]) is list and [x["table"] for x in proof["files"]] == list(TABLES),
        "Full V3 source table roster required",
    )
    need(
        type(proof["total_bytes"]) is int and 0 <= proof["total_bytes"] <= CAPTURE_BYTES,
        "V3 capture size differs",
    )
    total = 0
    for entry in proof["files"]:
        checkpoint()
        need(
            set(entry)
            == {
                "table",
                "presence",
                "file",
                "rows",
                "bytes",
                "sha256",
                "columns",
                "capture_order",
                "identity_key",
            },
            "Exact V3 table description required",
        )
        table = entry["table"]
        need(
            entry["file"] == table + ".pgcopy"
            and entry["columns"] == columns[table]
            and entry["identity_key"] == ["id"]
            and entry["capture_order"] == "physical_heap_ranges",
            "V3 table source identity differs",
        )
        need(
            type(entry["rows"]) is int
            and entry["rows"] >= 0
            and type(entry["bytes"]) is int
            and len(HEADER) + 2 <= entry["bytes"] <= CAPTURE_BYTES
            and entry["presence"] == ("present_nonempty" if entry["rows"] else "present_empty")
            and isinstance(entry["sha256"], str)
            and re.fullmatch("[a-f0-9]{64}", entry["sha256"]),
            "V3 table presence or size differs",
        )
        if verify_files:
            path = source / entry["file"]
            _private(path, entry["bytes"])
            need(
                _hash(path, checkpoint, cooperative_pause=cooperative_pause) == entry["sha256"],
                "V3 native table hash differs",
            )
        total += entry["bytes"]
    need(total == proof["total_bytes"], "V3 capture byte total differs")
    return proof


def _exact(f, size):
    need(0 <= size <= ROW_BYTES, "V3 native field exceeds bounded row")
    raw = f.read(size)
    need(len(raw) == size, "Truncated V3 native data")
    return raw


def _row(f, count):
    raw = _exact(f, 2)
    n = struct.unpack("!h", raw)[0]
    if n == -1:
        return None
    need(n == count, "V3 native row column count differs")
    out = bytearray(raw)
    for _ in range(count):
        length_raw = _exact(f, 4)
        length = struct.unpack("!i", length_raw)[0]
        need(
            -1 <= length <= ROW_BYTES and len(out) + 4 + max(length, 0) <= ROW_BYTES,
            "V3 native row bound differs",
        )
        out.extend(length_raw)
        if length >= 0:
            out.extend(_exact(f, length))
    return bytes(out)


def _write_immutable(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    with os.fdopen(fd, "wb") as f:
        os.fchmod(f.fileno(), 0o400)
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _part_entry(table, sequence, first, rows, start, end, payload, digest):
    complete = digest.copy()
    complete.update(TRAILER)
    return {
        "table": table,
        "sequence": sequence,
        "first_row": first,
        "rows": rows,
        "start": start,
        "end": end,
        "bytes": len(HEADER) + payload + len(TRAILER),
        "sha256": complete.hexdigest(),
    }


def prepare_index(source, capture_sha256, output_file, *, checkpoint):
    """Create only a small immutable part index; no duplicated whole-source spool."""
    need(callable(checkpoint), "Bounded offline checkpoint required")
    source = Path(source)
    output_file = Path(output_file)
    need(not os.path.lexists(output_file), "New V3 part inventory required")
    parent = output_file.parent.lstat()
    need(
        stat.S_ISDIR(parent.st_mode)
        and parent.st_uid == os.geteuid()
        and stat.S_IMODE(parent.st_mode) == 0o700,
        "Private V3 index directory required",
    )
    proof = load_capture(source, capture_sha256, checkpoint=checkpoint)
    parts = []
    for entry in proof["files"]:
        table = entry["table"]
        origin = source / entry["file"]
        before = _private(origin, entry["bytes"])
        with origin.open("rb") as f:
            info = os.fstat(f.fileno())
            need(
                (before.st_dev, before.st_ino) == (info.st_dev, info.st_ino),
                "V3 input generation differs",
            )
            need(_exact(f, len(HEADER)) == HEADER, "V3 COPY header differs")
            table_parts = []
            count = 0
            part_start = len(HEADER)
            first = 0
            payload = 0
            rows = 0
            h = hashlib.sha256(HEADER)

            while True:
                checkpoint()
                start = f.tell()
                row = _row(f, len(entry["columns"]))
                if row is None:
                    need(not f.read(1) and f.tell() == entry["bytes"], "V3 COPY trailer differs")
                    table_parts.append(
                        _part_entry(
                            table, len(table_parts), first, rows, part_start, start, payload, h
                        )
                    )
                    break
                need(
                    len(HEADER) + len(row) + len(TRAILER) <= MAX_PART_BYTES,
                    "Single V3 row exceeds part budget",
                )
                if rows and (
                    rows >= MAX_PART_ROWS
                    or len(HEADER) + payload + len(row) + len(TRAILER) > MAX_PART_BYTES
                ):
                    table_parts.append(
                        _part_entry(
                            table, len(table_parts), first, rows, part_start, start, payload, h
                        )
                    )
                    part_start = start
                    first = count
                    payload = rows = 0
                    h = hashlib.sha256(HEADER)
                h.update(row)
                payload += len(row)
                rows += 1
                count += 1
            after = os.fstat(f.fileno())
            need(
                (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                == (after.st_size, after.st_mtime_ns, after.st_ctime_ns),
                "V3 source changed during indexing",
            )
        need(count == entry["rows"], "V3 native row count differs")
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
    raw = (json.dumps(index, sort_keys=True, separators=(",", ":")) + "\n").encode()
    need(len(raw) <= MAX_MANIFEST, "V3 part inventory exceeds budget")
    _write_immutable(output_file, raw)
    return {"index": index, "index_sha256": hashlib.sha256(raw).hexdigest()}


def load_index(
    source,
    capture_sha256,
    index_file,
    index_sha256,
    *,
    checkpoint,
    verify_files=True,
    cooperative_pause=0.0,
):
    proof = load_capture(
        Path(source),
        capture_sha256,
        checkpoint=checkpoint,
        verify_files=verify_files,
        cooperative_pause=cooperative_pause,
    )
    raw = _bytes(Path(index_file), MAX_MANIFEST)
    need(
        isinstance(index_sha256, str)
        and re.fullmatch("[a-f0-9]{64}", index_sha256)
        and hashlib.sha256(raw).hexdigest() == index_sha256,
        "Pinned V3 part inventory differs",
    )
    index = json.loads(raw)
    return proof, validate_index(proof, index, capture_sha256)


def validate_index(proof, index, capture_sha256):
    """Validate ordered coverage without requiring the native Source files."""
    keys = {
        "format",
        "capture_id",
        "capture_sha256",
        "scope_sha256",
        "complete",
        "max_part_bytes",
        "max_part_rows",
        "parts",
        "parent_fk_closure_proven",
        "authenticated",
    }
    need(
        type(index) is dict
        and set(index) == keys
        and index["format"] == PART_FORMAT
        and index["capture_id"] == proof["capture_id"]
        and index["capture_sha256"] == capture_sha256
        and index["scope_sha256"] == SOURCE_SCOPE_SHA256
        and index["complete"] is True
        and index["max_part_bytes"] == MAX_PART_BYTES
        and index["max_part_rows"] == MAX_PART_ROWS
        and index["parent_fk_closure_proven"] is False
        and index["authenticated"] is False,
        "V3 part inventory contract differs",
    )
    parts = index["parts"]
    need(type(parts) is list and len(parts) >= 2, "V3 table parts required")
    cursor = 0
    for entry in proof["files"]:
        table = entry["table"]
        offset = len(HEADER)
        first = 0
        seq = 0
        while cursor < len(parts) and parts[cursor].get("table") == table:
            p = parts[cursor]
            need(
                type(p) is dict
                and set(p)
                == {"table", "sequence", "first_row", "rows", "start", "end", "bytes", "sha256"}
                and all(
                    type(p[k]) is int
                    for k in ("sequence", "first_row", "rows", "start", "end", "bytes")
                )
                and p["sequence"] == seq
                and p["first_row"] == first
                and p["start"] == offset
                and 0 <= p["rows"] <= MAX_PART_ROWS
                and p["end"] >= offset
                and p["bytes"] == len(HEADER) + (p["end"] - offset) + len(TRAILER)
                and p["bytes"] <= MAX_PART_BYTES
                and isinstance(p["sha256"], str)
                and re.fullmatch("[a-f0-9]{64}", p["sha256"]),
                "V3 part gap, overlap or identity differs",
            )
            need((p["rows"] == 0) == (entry["rows"] == 0 and seq == 0), "Unexpected empty V3 part")
            offset = p["end"]
            first += p["rows"]
            seq += 1
            cursor += 1
        need(
            seq > 0 and offset == entry["bytes"] - len(TRAILER) and first == entry["rows"],
            "Incomplete V3 source table coverage",
        )
    need(cursor == len(parts), "Unexpected V3 source table part")
    return index


def materialize_part(
    source, capture_sha256, index_file, index_sha256, position, output, *, checkpoint
):
    """Create one <=32 MiB native part, bound to one completed capture and index."""
    need(callable(checkpoint), "Bounded offline checkpoint required")
    source = Path(source)
    output = Path(output)
    need(not os.path.lexists(output), "New private V3 part required")
    parent = output.parent.lstat()
    need(
        stat.S_ISDIR(parent.st_mode)
        and parent.st_uid == os.geteuid()
        and stat.S_IMODE(parent.st_mode) == 0o700,
        "Private V3 part directory required",
    )
    proof, index = load_index(
        source, capture_sha256, index_file, index_sha256, checkpoint=checkpoint
    )
    need(
        type(position) is int and 0 <= position < len(index["parts"]),
        "Exact V3 part position required",
    )
    part = index["parts"][position]
    usage = shutil.disk_usage(output.parent)
    need(
        usage.free >= max(2 * 1024**3, (usage.total + 19) // 20) + part["bytes"],
        "Offline V3 source disk reserve unavailable",
    )
    need(
        part["bytes"] <= MAX_PART_BYTES and part["bytes"] * 4 <= MAX_ARCHIVE_WORKSET,
        "V3 archive working set exceeded",
    )
    table = next(x for x in proof["files"] if x["table"] == part["table"])
    h = hashlib.sha256()
    counter = FrameCounter(len(table["columns"]))
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        origin = source / table["file"]
        before = _private(origin, table["bytes"])
        origin_fd = os.open(origin, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "wb") as dest, os.fdopen(origin_fd, "rb") as f:
            current = os.fstat(f.fileno())
            need(
                (before.st_dev, before.st_ino) == (current.st_dev, current.st_ino),
                "V3 source generation differs",
            )
            dest.write(HEADER)
            h.update(HEADER)
            counter.feed(HEADER)
            f.seek(part["start"])
            left = part["end"] - part["start"]
            while left:
                checkpoint()
                block = f.read(min(65536, left))
                need(bool(block), "Truncated V3 source range")
                dest.write(block)
                h.update(block)
                counter.feed(block)
                left -= len(block)
            dest.write(TRAILER)
            h.update(TRAILER)
            counter.feed(TRAILER)
            need(counter.finish() == part["rows"], "V3 native part row count differs")
            after = os.fstat(f.fileno())
            need(
                (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                == (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                and h.hexdigest() == part["sha256"],
                "V3 part content or source generation differs",
            )
            dest.flush()
            os.fsync(dest.fileno())
            os.fchmod(dest.fileno(), 0o400)
            os.fsync(dest.fileno())
    except BaseException:
        # Preserve partial own output for operator diagnosis; never treat it complete.
        raise
    return {
        "format": "greenmind.science-part-transfer.v3",
        "capture_id": proof["capture_id"],
        "capture_sha256": capture_sha256,
        "index_sha256": index_sha256,
        "position": position,
        "table": part["table"],
        "bytes": part["bytes"],
        "sha256": part["sha256"],
        "authenticated": False,
        "published": False,
        "restored": False,
    }


class ArchivePartHooks(Protocol):
    """Unimplemented, root-only interfaces for the existing archive pipeline.

    Every returned record must bind the complete capture and index digests,
    globally ordered position and exact native plaintext SHA. Implementations
    must persist/check intents and reconcile unknown outcomes before retry.
    """

    def receive_private_part(self, descriptor: dict, target: Path, *, checkpoint) -> dict:
        """Receive the new science-part-authorized stream; enforce bytes and hash."""
        ...

    def encrypt_part(
        self, descriptor: dict, plaintext: Path, ciphertext: Path, *, checkpoint
    ) -> dict:
        """Archive Root only: use existing backup.encrypt and bind the descriptor."""
        ...

    def publish_once(self, descriptor: dict, ciphertext: Path, *, checkpoint) -> dict:
        """Use the existing Store and durable journal; never repeat an unknown upload."""
        ...

    def independent_readback(self, descriptor: dict, receipt: dict, *, checkpoint) -> dict:
        """Retrieve from the remote store, authenticate/decrypt, and rehash native bytes."""
        ...

    def isolated_native_restore(self, descriptor: dict, readback: dict, *, checkpoint) -> dict:
        """Compare all native values and PKs in a new isolated typed staging table."""
        ...

    def authenticate_whole_inventory(
        self, index: dict, verified_parts: list, *, checkpoint
    ) -> dict:
        """Bind every ordered part receipt; do not assert global parent/FK closure."""
        ...


def archive_integration_contract():
    """Required next wiring; this function performs no encryption or publication."""
    return {
        "format": "greenmind.science-archive-integration.v3",
        "part_transport": "existing-root-private-binary-bridge-with-new-science-part-authority",
        "seal": "app.tiered_archive.backup.encrypt",
        "verify": "independent ciphertext readback plus backup.decrypt and exact native part hash",
        "publish": "existing bounded Store and journal with unknown-outcome reconciliation",
        "restore": "isolated native PG staging table with exact types and full row/PK content comparison",
        "whole_manifest": "authenticate ordered capture and part inventory plus every verified part receipt",
        "implemented": False,
        "parent_fk_closure_proven": False,
    }
