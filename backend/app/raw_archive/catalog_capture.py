"""Fast column-limited snapshot; scientific validation runs after SQL closes.

COPY TO STDOUT uses PostgreSQL's native network format. This
preserves NULL versus empty text without a CSV parser or unsafe deserialization.
No SQL writes, wider credentials, longer source leases or in-memory dataset.
"""

import base64
import hashlib
import json
import os
import shutil
import struct
import time
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy import text

from . import catalog_stages, wav_catalog
from .policy import ArchiveBlocked, checksum

SIGNATURE = b"PGCOPY\n\xff\r\n\x00"
CAPTURE_BYTES = 2 * 1024**3
HEAP_BLOCKS = 1024  # Eight MiB per SQL statement; original eight-second timeout.
TYPES = {
    "uuid",
    "text",
    "varchar",
    "bpchar",
    "int2",
    "int4",
    "int8",
    "float4",
    "float8",
    "numeric",
    "bool",
    "timestamptz",
    "timestamp",
    "date",
    "json",
    "jsonb",
    "bytea",
}
GATEWAY_KEYS = {
    **{
        name: ("id",)
        for name in (
            "organization",
            "zone",
            "gateway",
            "sensor",
            "wav_file",
            "wav_feature",
            "wav_feature_version",
        )
    },
    "visual_reading": ("sensor_id", "kind", "bucket", "seconds"),
    "visual_wav": ("wav_id",),
    "visual_wave": ("wav_id", "bucket", "seconds"),
    "visual_chunk": ("chunk_name",),
}


class CopyRowsSink:
    """Join independently bounded COPY streams without losing binary framing."""

    def __init__(self, sink):
        self.sink, self.header, self.tail = sink, bytearray(), b""

    def write(self, block):
        size = len(block)
        if size > wav_catalog.ROW_BYTES * 2:
            raise ArchiveBlocked("Capture transfer block exceeds its memory budget")
        needed = 19 - len(self.header)
        if needed:
            self.header.extend(block[:needed])
            block = block[needed:]
            if len(self.header) == 19 and bytes(self.header) != SIGNATURE + struct.pack(
                "!ii", 0, 0
            ):
                raise ArchiveBlocked("Unsupported COPY chunk header")
        data = self.tail + block
        if len(data) > 2:
            self.sink.write(data[:-2])
        self.tail = data[-2:]
        return size

    def finish(self):
        if len(self.header) != 19 or self.tail != struct.pack("!h", -1):
            raise ArchiveBlocked("Incomplete COPY chunk")


def copy_gateway_chunks(db, cursor, table, fields, sink, body, checkpoint):
    """One consistent snapshot, indexed pages, original eight-second statements."""
    keys = GATEWAY_KEYS[table]
    actual = tuple(
        db.execute(
            text("""SELECT a.attname FROM pg_index i
        CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum,position)
        JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum
        WHERE i.indrelid=to_regclass(:table) AND i.indisprimary
        ORDER BY k.position"""),
            {"table": "public." + table},
        ).scalars()
    )
    if actual != keys or not set(keys) <= set(fields):
        raise ArchiveBlocked("Bounded capture requires the exact existing primary key")
    identifiers = ",".join('"' + key + '"' for key in keys)
    selected = ",".join('"' + name + '"' for name in fields)
    relation = '"public"."' + table + '"'
    marker = "(" + ",".join(["%s"] * len(keys)) + ")"
    lower, count = None, 0
    sink.write(SIGNATURE + struct.pack("!ii", 0, 0))
    while True:
        checkpoint()
        where = "" if lower is None else " WHERE (" + identifiers + ")>" + marker
        cursor.execute(
            "SELECT "
            + identifiers
            + " FROM "
            + relation
            + where
            + " ORDER BY "
            + identifiers
            + " LIMIT 5000",
            () if lower is None else lower,
        )
        page = cursor.fetchall()
        if not page:
            break
        upper = page[-1]
        condition = "(" + identifiers + ")<=" + marker
        values = upper
        if lower is not None:
            condition += " AND (" + identifiers + ")>" + marker
            values += lower
        query = cursor.mogrify(
            "COPY (SELECT "
            + selected
            + " FROM "
            + relation
            + " WHERE "
            + condition
            + ") TO STDOUT (FORMAT binary)",
            values,
        )
        chunk = CopyRowsSink(sink)
        cursor.copy_expert(query.decode(), chunk)
        chunk.finish()
        if cursor.rowcount != len(page):
            raise ArchiveBlocked("COPY chunk differs from the same-snapshot key page")
        count += cursor.rowcount
        lower = upper
        body.flush()
        os.fsync(body.fileno())
        if hasattr(os, "posix_fadvise"):
            os.posix_fadvise(body.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
        # Yield between statements, outside SQL and inside the original lease.
        # Receiver guards are checked again before the next indexed page.
        time.sleep(0.05)
    sink.write(struct.pack("!h", -1))
    return count


def stream_gateway_table(db, table, fields, descriptors, sink, checkpoint):
    """One sequential scan, bounded FETCH statements, raw text on archive host.

    The cursor lives in the same read-only repeatable-read transaction as all
    other tables. Raw typecasters defer JSON/scientific decoding until SQL has
    closed. No system-column grants or source files are needed.
    """
    from psycopg2.extensions import new_type, register_type

    raw = db.connection.driver_connection
    cursor = raw.cursor(name="greenmind_wav_catalog_" + table)
    cursor.itersize = cursor.arraysize = 1000
    oids = tuple(item[3] for item in descriptors)
    register_type(new_type(oids, "GREENMIND_CATALOG_RAW_TEXT", lambda value, _: value), cursor)
    # PostgreSQL's bool protocol text is t/f; explicit text preserves the existing
    # capture format's true/false representation without decoding other fields.
    types = {item[0]: item[1] for item in descriptors}
    selected = ",".join(
        '"' + name + '"' + ("::text" if types[name] == "bool" else "") for name in fields
    )
    sink.write(SIGNATURE + struct.pack("!ii", 0, 0))
    count, synced_bytes = 0, 0
    try:
        checkpoint()
        cursor.execute("SELECT " + selected + ' FROM "public"."' + table + '"')
        while True:
            checkpoint()
            rows = cursor.fetchmany(1000)
            if not rows:
                break
            for row in rows:
                if len(row) != len(fields):
                    raise ArchiveBlocked("Cursor field coverage differs")
                framed = [struct.pack("!h", len(fields))]
                row_bytes = 2
                for value in row:
                    if value is not None and not isinstance(value, str):
                        raise ArchiveBlocked("Archive capture requires raw SQL text")
                    value = None if value is None else value.encode("utf-8")
                    if value is not None and len(value) > wav_catalog.ROW_BYTES:
                        raise ArchiveBlocked("Capture field exceeds its memory budget")
                    row_bytes += 4 + (0 if value is None else len(value))
                    if row_bytes > wav_catalog.ROW_BYTES * 2:
                        raise ArchiveBlocked("Capture row exceeds its memory budget")
                    framed.append(struct.pack("!i", -1 if value is None else len(value)))
                    if value is not None:
                        framed.append(value)
                # Hash/guard/write once per row, not once per field. The bounded
                # frame contains raw SQL bytes; scientific parsing stays offline.
                sink.write(b"".join(framed))
                count += 1
            if sink.written - synced_bytes >= 16 * 1024**2:
                sink.body.flush()
                os.fsync(sink.body.fileno())
                if hasattr(os, "posix_fadvise"):
                    os.posix_fadvise(sink.body.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
                synced_bytes = sink.written
            # Cooperative yield happens outside each FETCH statement. The source
            # statement timeout and original whole-snapshot deadline still apply.
            time.sleep(0.005)
        sink.write(struct.pack("!h", -1))
        return count
    finally:
        cursor.close()


def copy_gateway_blocks(db, cursor, table, fields, sink, body, checkpoint):
    """Native sequential heap ranges within one unchanged SQL snapshot.

    CTID is used only as a physical scan boundary, never as recording identity.
    Existing logical UUIDs and all allowed fields remain unchanged. ACCESS SHARE
    from the empty allowed-column query fences table rewrites without blocking
    ingestion. MVCC retains visible tuples until this transaction ends.
    """
    relation = '"public"."' + table + '"'
    selected = ",".join('"' + name + '"' for name in fields)
    db.execute(text("SELECT " + selected + " FROM " + relation + " WHERE false"))
    size, block_size, kind, inherited = db.execute(
        text("""SELECT
        pg_relation_size(c.oid),current_setting('block_size')::int,c.relkind,
        EXISTS(SELECT 1 FROM pg_inherits WHERE inhparent=c.oid OR inhrelid=c.oid)
        FROM pg_class c WHERE c.oid=to_regclass(:table)"""),
        {"table": "public." + table},
    ).one()
    if kind != "r" or inherited or block_size != 8192 or size < 0:
        raise ArchiveBlocked("Exact plain heap tables required for physical range capture")
    blocks = (size + block_size - 1) // block_size
    sink.write(SIGNATURE + struct.pack("!ii", 0, 0))
    count = 0
    for begin in range(0, blocks, HEAP_BLOCKS):
        checkpoint()
        end = min(begin + HEAP_BLOCKS, blocks)
        query = cursor.mogrify(
            "COPY (SELECT "
            + selected
            + " FROM "
            + relation
            + " WHERE ctid >= %s::tid AND ctid < %s::tid) TO STDOUT (FORMAT binary)",
            (f"({begin},0)", f"({end},0)"),
        )
        chunk = CopyRowsSink(sink)
        cursor.copy_expert(query.decode(), chunk)
        chunk.finish()
        if cursor.rowcount < 0:
            raise ArchiveBlocked("Physical COPY range lacks its exact count")
        count += cursor.rowcount
        body.flush()
        os.fsync(body.fileno())
        if hasattr(os, "posix_fadvise"):
            os.posix_fadvise(body.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
        # Yield source I/O between native ranges; keep the 110s lease unchanged.
        time.sleep(0.05)
    sink.write(struct.pack("!h", -1))
    return count


class Sink:
    def __init__(self, body, checkpoint, budget):
        self.body, self.checkpoint, self.budget = body, checkpoint, budget
        self.written = 0
        self.digest = hashlib.sha256()
        self.last = float("-inf")

    def write(self, block):
        if len(block) > wav_catalog.ROW_BYTES * 2:
            raise ArchiveBlocked("Capture transfer block exceeds its memory budget")
        if self.written + len(block) > self.budget:
            raise ArchiveBlocked("Capture exceeds its disk budget")
        if time.monotonic() - self.last >= 1:
            self.checkpoint()
            self.last = time.monotonic()
        self.body.write(block)
        self.digest.update(block)
        self.written += len(block)
        return len(block)


def capture(engine, kind, output, checkpoint):
    if kind not in wav_catalog.COLUMNS or output.exists():
        raise ArchiveBlocked("New output and exact allowlisted source required")
    checkpoint()
    wav_catalog.private_directory(output)
    disk = shutil.disk_usage(output)
    if disk.free < max(2 * 1024**3, (disk.total + 19) // 20) + CAPTURE_BYTES:
        raise ArchiveBlocked("Capture must preserve existing disk reserves")
    started = datetime.now(UTC).isoformat()
    files, written = [], 0
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as db:
        with db.begin():
            if db.execute(text("SHOW transaction_read_only")).scalar_one() != "on":
                raise ArchiveBlocked("Capture source must be read-only")
            db.execute(text("SET LOCAL TIME ZONE 'UTC'"))
            db.execute(text("SET LOCAL DateStyle='ISO, YMD'"))
            db.execute(text("SET LOCAL bytea_output='hex'"))
            snapshot = db.execute(text("SELECT pg_current_snapshot()::text")).scalar_one()
            cursor = db.connection.driver_connection.cursor()
            try:
                for table, fields in wav_catalog.COLUMNS[kind].items():
                    checkpoint()
                    # Catalog names are parameters; selected identifiers come only
                    # from the immutable WAV-only column allowlist.
                    descriptors = db.execute(
                        text("""SELECT a.attname,t.typname,t.typtype,t.oid FROM pg_attribute a
                        JOIN pg_type t ON t.oid=a.atttypid
                        WHERE a.attrelid=to_regclass(:table) AND NOT a.attisdropped
                        AND a.attnum>0"""),
                        {"table": "public." + table},
                    ).all()
                    types = {name: (typ, category) for name, typ, category, _ in descriptors}
                    if any(name not in types for name in fields):
                        raise ArchiveBlocked("Capture source lacks required columns")
                    selected = []
                    for name in fields:
                        typ, category = types[name]
                        if typ not in TYPES and category != "e":
                            raise ArchiveBlocked("Unsupported capture type")
                        selected.append("text" if category == "e" else typ)
                    statement = (
                        "COPY (SELECT "
                        + ",".join('"' + name + '"' for name in fields)
                        + ' FROM "public"."'
                        + table
                        + '") TO STDOUT (FORMAT binary)'
                    )
                    path = output / (table + ".pgcopy")
                    with path.open("xb") as body:
                        os.fchmod(body.fileno(), 0o600)
                        sink = Sink(body, checkpoint, CAPTURE_BYTES - written)
                        if kind == "gateway":
                            rowcount = copy_gateway_blocks(
                                db, cursor, table, fields, sink, body, checkpoint
                            )
                        else:
                            cursor.copy_expert(statement, sink)
                            rowcount = cursor.rowcount
                        checkpoint()
                        body.flush()
                        os.fsync(body.fileno())
                    if rowcount < 0:
                        raise ArchiveBlocked("COPY did not report an exact row count")
                    written += sink.written
                    files.append(
                        {
                            "table": table,
                            "file": path.name,
                            "types": selected,
                            "rows": rowcount,
                            "bytes": sink.written,
                            "sha256": sink.digest.hexdigest(),
                        }
                    )
            finally:
                cursor.close()
    checkpoint()
    finished = datetime.now(UTC).isoformat()
    if (datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds() > 120:
        raise ArchiveBlocked("Capture exceeded the original source budget")
    proof = {
        "schema": 1,
        "format": "wav_pgcopy_native_v2",
        "kind": kind,
        "allowlist_sha256": wav_catalog.ALLOWLIST_SHA256,
        "started_at": started,
        "finished_at": finished,
        "snapshot": snapshot,
        "files": files,
        "source_read_only": True,
        "complete": True,
        "decoded_validation_complete": False,
        "deleted_files": 0,
    }
    wav_catalog.save_json(output / "capture.json", proof)
    return proof


def read_exact(body, size):
    if not 0 <= size <= wav_catalog.ROW_BYTES:
        raise ArchiveBlocked("Capture field exceeds its memory budget")
    value = body.read(size)
    if len(value) != size:
        raise ArchiveBlocked("Truncated capture")
    return value


def decoded(value, typ):
    if value is None:
        return None
    text_value = value.decode("utf-8", errors="strict")
    if typ in {"int2", "int4", "int8"}:
        return int(text_value)
    if typ in {"float4", "float8"}:
        return float(text_value)
    if typ == "bool":
        if text_value not in {"true", "false"}:
            raise ArchiveBlocked("Invalid capture boolean")
        return text_value == "true"
    if typ in {"json", "jsonb"}:
        return json.loads(text_value)
    if typ == "bytea":
        if not text_value.startswith("\\x"):
            raise ArchiveBlocked("Capture bytea is not hexadecimal")
        return {"__bytea__": base64.b64encode(bytes.fromhex(text_value[2:])).decode("ascii")}
    if typ in {"timestamp", "timestamptz"}:
        stamp = datetime.fromisoformat(text_value)
        if (stamp.tzinfo is not None) != (typ == "timestamptz"):
            raise ArchiveBlocked("Capture timestamp timezone differs")
        return str(stamp)
    if typ not in TYPES:
        raise ArchiveBlocked("Unsupported capture type")
    return text_value


def native_decoded(value, typ):
    """Decode PostgreSQL's documented network format on the isolated host."""
    if value is None:
        return None
    formats = {"int2": "!h", "int4": "!i", "int8": "!q", "float4": "!f", "float8": "!d"}
    if typ in formats:
        if len(value) != struct.calcsize(formats[typ]):
            raise ArchiveBlocked("Invalid native numeric field length")
        return struct.unpack(formats[typ], value)[0]
    if typ == "uuid":
        if len(value) != 16:
            raise ArchiveBlocked("Invalid native UUID field length")
        return str(UUID(bytes=value))
    if typ == "bool":
        if value not in (b"\x00", b"\x01"):
            raise ArchiveBlocked("Invalid native boolean")
        return value == b"\x01"
    if typ == "bytea":
        return {"__bytea__": base64.b64encode(value).decode()}
    if typ in {"timestamp", "timestamptz"}:
        if len(value) != 8:
            raise ArchiveBlocked("Invalid native timestamp field length")
        stamp = datetime(2000, 1, 1, tzinfo=UTC if typ == "timestamptz" else None)
        return (stamp + timedelta(microseconds=struct.unpack("!q", value)[0])).isoformat()
    if typ == "date":
        if len(value) != 4:
            raise ArchiveBlocked("Invalid native date field length")
        return (date(2000, 1, 1) + timedelta(days=struct.unpack("!i", value)[0])).isoformat()
    if typ == "jsonb":
        if not value.startswith(b"\x01"):
            raise ArchiveBlocked("Unsupported native JSONB version")
        return json.loads(value[1:])
    if typ == "numeric":
        if len(value) < 8:
            raise ArchiveBlocked("Invalid native decimal header")
        digits, weight, sign, scale = struct.unpack("!hhHH", value[:8])
        if (
            not 0 <= digits <= 4096
            or len(value) != 8 + 2 * digits
            or sign not in (0, 0x4000)
            or scale > 16383
            or abs(weight) > 4096
        ):
            raise ArchiveBlocked("Unsupported native decimal representation")
        values = struct.unpack("!" + "H" * digits, value[8:])
        if any(d >= 10000 for d in values):
            raise ArchiveBlocked("Invalid native decimal digit")
        chunks = {weight - i: f"{d:04d}" for i, d in enumerate(values)}
        whole = (
            "".join(chunks.get(i, "0000") for i in range(max(weight, 0), -1, -1)).lstrip("0") or "0"
        )
        fraction = "".join(chunks.get(-i, "0000") for i in range(1, (scale + 3) // 4 + 1))[:scale]
        return (
            ("-" if sign == 0x4000 and any(values) else "")
            + whole
            + ("." + fraction if scale else "")
        )
    if typ in {"text", "varchar", "bpchar", "json"}:
        return decoded(value, typ)
    raise ArchiveBlocked("Unsupported native capture type")


def rows(path, types, *, native=False):
    with path.open("rb") as body:
        if read_exact(body, len(SIGNATURE)) != SIGNATURE:
            raise ArchiveBlocked("Invalid PostgreSQL COPY signature")
        flags, extension = struct.unpack("!ii", read_exact(body, 8))
        if flags != 0 or extension != 0:
            raise ArchiveBlocked("Unexpected COPY framing flags")
        while True:
            count = struct.unpack("!h", read_exact(body, 2))[0]
            if count == -1:
                if body.read(1):
                    raise ArchiveBlocked("Trailing capture bytes")
                return
            if count != len(types):
                raise ArchiveBlocked("Capture column count differs")
            result, size = [], 0
            for typ in types:
                length = struct.unpack("!i", read_exact(body, 4))[0]
                if length < -1:
                    raise ArchiveBlocked("Invalid negative COPY length")
                size += max(0, length)
                if size > wav_catalog.ROW_BYTES:
                    raise ArchiveBlocked("Capture row exceeds its memory budget")
                decoder = native_decoded if native else decoded
                result.append(decoder(None if length == -1 else read_exact(body, length), typ))
            yield result


def process(source, output, checkpoint=lambda: None):
    """Entirely offline; source windows remain the actual SQL capture windows."""
    info = source / "capture.json"
    if (
        info.is_symlink()
        or info.stat().st_size > wav_catalog.ROW_BYTES
        or info.stat().st_uid != os.getuid()
        or info.stat().st_mode & 0o077
    ):
        raise ArchiveBlocked("Bounded completed capture proof required")
    proof = json.loads(info.read_text())
    kind = proof.get("kind")
    if (
        proof.get("schema") != 1
        or proof.get("format") not in {"wav_pgcopy_text_v1", "wav_pgcopy_native_v2"}
        or proof.get("complete") is not True
        or proof.get("source_read_only") is not True
        or proof.get("allowlist_sha256") != wav_catalog.ALLOWLIST_SHA256
        or kind not in wav_catalog.COLUMNS
        or output.exists()
    ):
        raise ArchiveBlocked("New output and completed allowlisted capture required")
    entries = proof["files"]
    begin, end = (datetime.fromisoformat(proof[key]) for key in ("started_at", "finished_at"))
    if begin.tzinfo is None or end.tzinfo is None or not 0 <= (end - begin).total_seconds() <= 120:
        raise ArchiveBlocked("Capture exceeded the original source budget")
    if len(entries) != len(wav_catalog.COLUMNS[kind]) or {e["table"] for e in entries} != set(
        wav_catalog.COLUMNS[kind]
    ):
        raise ArchiveBlocked("Capture omits or duplicates tables")
    if (
        any(
            type(e["rows"]) is not int
            or e["rows"] < 0
            or type(e["bytes"]) is not int
            or e["bytes"] <= 0
            or any(typ not in TYPES for typ in e["types"])
            for e in entries
        )
        or sum(e["bytes"] for e in entries) > CAPTURE_BYTES
    ):
        raise ArchiveBlocked("Capture exceeded its disk budget")
    wav_catalog.private_directory(output)
    parts = wav_catalog.Parts(output, kind, checkpoint, compresslevel=1)
    counts = {}
    try:
        for entry in entries:
            checkpoint()
            table = entry["table"]
            if entry["file"] != table + ".pgcopy":
                raise ArchiveBlocked("Unsafe capture filename")
            path = source / entry["file"]
            if (
                path.is_symlink()
                or path.stat().st_size != entry["bytes"]
                or path.stat().st_uid != os.getuid()
                or path.stat().st_mode & 0o077
                or checksum(path) != entry["sha256"]
            ):
                raise ArchiveBlocked("Capture bytes changed")
            fields = wav_catalog.COLUMNS[kind][table]
            if len(fields) != len(entry["types"]):
                raise ArchiveBlocked("Capture columns differ")
            count = 0
            for values in rows(
                path, entry["types"], native=proof["format"] == "wav_pgcopy_native_v2"
            ):
                parts.write(
                    wav_catalog.validate_row(kind, table, dict(zip(fields, values, strict=True)))
                )
                count += 1
            if count != entry["rows"]:
                raise ArchiveBlocked("Decoded capture count differs from source COPY")
            counts[table] = count
    finally:
        parts.finish()
    stage = catalog_stages.completed_stage(
        kind,
        proof["started_at"],
        parts.files,
        counts,
        finished_at=proof["finished_at"],
        processed_at=datetime.now(UTC).isoformat(),
        capture_sha256=checksum(info),
        **(
            {"direct_bucket": os.environ.get("RAW_ARCHIVE_DIRECT_S3_BUCKET", "")}
            if kind == "direct"
            else {}
        ),
    )
    catalog_stages.validate_stage(stage, kind, output)
    wav_catalog.save_json(output / "stage.json", stage)
    return stage
