"""Exact parent-only native COPY capture/parts using existing archive encodings.

Caller supplies the existing admitted Source reader engine and guards. No engine,
grant, process, daemon, encryption key or network connection is created here.
"""

import hashlib
import json
import os
import re
import stat
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

from . import catalog_capture, scientific_capture, scientific_parts
from .parent_scope import SCOPE, SCOPE_SHA256
from .policy import ArchiveBlocked, checksum
from .scientific_capture import HEADER, TRAILER, digest_json

MAX_CAPTURE_BYTES = 512 * 1024**2
MAX_PART_BYTES = 16 * 1024**2
MAX_PART_ROWS = scientific_parts.MAX_PART_ROWS
DATABASES = {"gateway": "plantdb", "direct": "greenmind_direct_production"}


def need(value, reason):
    if not value:
        raise ArchiveBlocked(reason)


def scope(source):
    need(
        digest_json(SCOPE) == SCOPE_SHA256 and source in DATABASES, "Pinned parent catalog required"
    )
    return SCOPE["databases"][DATABASES[source]]


def file_identity(path):
    s = Path(path).lstat()
    need(
        stat.S_ISREG(s.st_mode)
        and s.st_uid == os.geteuid()
        and s.st_nlink == 1
        and stat.S_IMODE(s.st_mode) == 0o400,
        "Immutable private parent file required",
    )
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]


def _identity(db, source):
    expected = scope(source)["identity"]
    result = dict(
        db.execute(
            text("""SELECT current_database() AS database,
        (SELECT oid::bigint FROM pg_database WHERE datname=current_database()) AS database_oid,
        current_user AS role,session_user AS session_role,
        (SELECT oid::bigint FROM pg_roles WHERE rolname=current_user) AS role_oid,
        (SELECT rolcanlogin AND NOT(rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)
          FROM pg_roles WHERE rolname=current_user) AS role_unprivileged,
        current_setting('server_version_num')::int AS server_version_num,
        inet_server_addr()::text AS server_address,inet_server_port() AS port,
        pg_postmaster_start_time()::text AS postmaster_start,
        current_setting('transaction_read_only') AS read_only,pg_is_in_recovery() AS in_recovery""")
        )
        .mappings()
        .one()
    )
    need(
        all(result[k] == v for k, v in expected.items())
        and result["role"] == result["session_role"] == "greenmind_archive_reader_33fe395"
        and result["role_oid"] == 499352
        and result["role_unprivileged"] is True
        and result["read_only"] == "on"
        and result["in_recovery"] is False,
        "Actual parent capture identity differs",
    )
    return result


def _schema(db, table):
    params = {"relation": "public." + table["name"]}
    columns = [
        dict(r)
        for r in db.execute(
            text("""SELECT a.attnum AS ordinal,a.attname AS name,
        a.atttypid::bigint AS type_oid,t.typname AS type,t.typtype::text AS type_kind,
        format_type(a.atttypid,a.atttypmod) AS formatted_type,a.attnotnull AS not_null,
        a.attidentity::text AS identity_kind,a.attgenerated::text AS generated_kind,
        pg_get_expr(d.adbin,d.adrelid) AS default_expression,
        has_column_privilege(current_user,a.attrelid,a.attnum,'SELECT') AS reader_column_select
        FROM pg_attribute a JOIN pg_type t ON t.oid=a.atttypid
        LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
        WHERE a.attrelid=to_regclass(:relation) AND a.attnum>0 AND NOT a.attisdropped
        ORDER BY a.attnum LIMIT 257"""),
            params,
        ).mappings()
    ]
    constraints = [
        dict(r)
        for r in db.execute(
            text("""SELECT k.conname AS name,k.contype::text AS type,
        pg_get_constraintdef(k.oid) AS definition,k.convalidated AS validated,
        k.condeferrable AS deferrable,k.condeferred AS deferred FROM pg_constraint k
        WHERE k.conrelid=to_regclass(:relation) ORDER BY k.conname LIMIT 129"""),
            params,
        ).mappings()
    ]
    indexes = [
        dict(r)
        for r in db.execute(
            text("""SELECT c.relname AS name,
        pg_get_indexdef(i.indexrelid) AS definition,i.indisprimary AS primary_index,
        i.indisunique AS unique_index,i.indisvalid AS valid,i.indisready AS ready
        FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
        WHERE i.indrelid=to_regclass(:relation) ORDER BY c.relname LIMIT 129"""),
            params,
        ).mappings()
    ]
    need(
        columns == table["columns"]
        and constraints == table["constraints"]
        and indexes == table["indexes"],
        "Actual full parent column/schema roster differs",
    )


def _save(path, value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    need(len(raw) <= 256 * 1024, "Parent metadata bound exceeded")
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_WRONLY, 0o400)
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    return hashlib.sha256(raw).hexdigest()


def capture(
    engine,
    source,
    output,
    *,
    checkpoint,
    lease_deadline,
    schema_master_sha256,
    provider_scope_sha256,
    exported_snapshot=None,
    counter_factory=None,
):
    """One actual source DB RR capture; no cross-database/science atomicity claim."""
    roster = scope(source)
    for value in (schema_master_sha256, provider_scope_sha256):
        need(
            type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value),
            "Pinned reference backup digests required",
        )
    if exported_snapshot is not None:
        need(
            type(exported_snapshot) is str
            and re.fullmatch(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{8}-[1-9][0-9]*", exported_snapshot),
            "Exact exported snapshot required",
        )
    scientific_capture._engine_binding(engine, DATABASES[source])
    output = Path(output)
    need(
        output.is_absolute() and not os.path.lexists(output), "New private parent capture required"
    )
    scientific_capture._source_resources(output.parent)
    output.mkdir(mode=0o700)
    start, started = time.monotonic(), datetime.now(UTC).isoformat()

    def check():
        need(
            time.monotonic() < min(start + 95, lease_deadline),
            "Parent capture lease/deadline exhausted",
        )
        scientific_capture._source_resources(output.parent)
        checkpoint()

    files, written = [], 0
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as db, db.begin():
        db.execute(text("SET TRANSACTION READ ONLY"))
        if exported_snapshot is not None:
            db.execute(text("SET TRANSACTION SNAPSHOT :snapshot"), {"snapshot": exported_snapshot})
        db.execute(text("SET LOCAL statement_timeout='8000ms'"))
        db.execute(text("SET LOCAL lock_timeout='150ms'"))
        db.execute(text("SET LOCAL TIME ZONE 'UTC'"))
        db.execute(text("SET LOCAL search_path=pg_catalog"))
        identity = _identity(db, source)
        snapshot = db.execute(text("SELECT pg_current_snapshot()::text")).scalar_one()
        cursor = db.connection.driver_connection.cursor()
        try:
            for table in roster["tables"]:
                check()
                _schema(db, table)
                path = output / (table["name"] + ".pgcopy")
                with path.open("xb") as body:
                    os.fchmod(body.fileno(), 0o600)
                    sink = (
                        scientific_capture.FramedSink(
                            body,
                            check,
                            MAX_CAPTURE_BYTES - written,
                            len(table["columns"]),
                            counter_factory=counter_factory,
                        )
                        if counter_factory
                        else catalog_capture.Sink(body, check, MAX_CAPTURE_BYTES - written)
                    )
                    rows = catalog_capture.copy_gateway_blocks(
                        db,
                        cursor,
                        table["name"],
                        [c["name"] for c in table["columns"]],
                        sink,
                        body,
                        check,
                        pause_seconds=0.05,
                    )
                    if counter_factory:
                        need(
                            sink.counter.finish() == rows,
                            "Parent native counter differs from PostgreSQL",
                        )
                    body.flush()
                    os.fsync(body.fileno())
                    os.fchmod(body.fileno(), 0o400)
                written += sink.written
                files.append(
                    {
                        "table": table["name"],
                        "file": path.name,
                        "columns": table["columns"],
                        "rows": rows,
                        "bytes": sink.written,
                        "sha256": sink.digest.hexdigest(),
                        "source_stat": file_identity(path),
                    }
                )
                _schema(db, table)
            need(_identity(db, source) == identity, "Parent source generation changed")
        finally:
            cursor.close()
    check()
    proof = {
        "schema": 1,
        "format": "wav_pgcopy_native_v2",
        "kind": source,
        "capture_id": uuid.uuid4().hex,
        "scope_sha256": SCOPE_SHA256,
        "metadata_sha256": SCOPE["metadata_sha256"],
        "source_identity": identity,
        "snapshot_sha256": hashlib.sha256(snapshot.encode()).hexdigest(),
        "imported_snapshot_sha256": hashlib.sha256(exported_snapshot.encode()).hexdigest()
        if exported_snapshot
        else None,
        "schema_master_sha256": schema_master_sha256,
        "provider_scope_sha256": provider_scope_sha256,
        "started_at": started,
        "finished_at": datetime.now(UTC).isoformat(),
        "files": files,
        "total_bytes": written,
        "source_read_only": True,
        "complete": True,
        "backup_anchor_reference_only": True,
        "backup_snapshot_consistency_proven": False,
        "cross_database_atomic": False,
        "same_snapshot_as_previous_science": False,
        "science_compatibility_proven": False,
    }
    return {"capture": proof, "capture_sha256": _save(output / "capture.json", proof)}


def load_capture(source, expected_sha256, *, verify_files=False, checkpoint=lambda: None):
    source = Path(source)
    path = source / "capture.json"
    file_identity(path)
    need(
        path.stat().st_size <= 256 * 1024 and checksum(path) == expected_sha256,
        "Parent capture metadata differs",
    )
    proof = json.loads(path.read_bytes())
    validate_capture(proof)
    for entry in proof["files"]:
        checkpoint()
        path = source / entry["file"]
        need(file_identity(path) == entry["source_stat"], "Parent source file generation differs")
        if verify_files:
            need(
                scientific_parts._hash(path, checkpoint) == entry["sha256"],
                "Parent native file differs",
            )
    return proof


def validate_capture(proof):
    fields = {
        "schema",
        "format",
        "kind",
        "capture_id",
        "scope_sha256",
        "metadata_sha256",
        "source_identity",
        "snapshot_sha256",
        "imported_snapshot_sha256",
        "schema_master_sha256",
        "provider_scope_sha256",
        "started_at",
        "finished_at",
        "files",
        "total_bytes",
        "source_read_only",
        "complete",
        "backup_anchor_reference_only",
        "backup_snapshot_consistency_proven",
        "cross_database_atomic",
        "same_snapshot_as_previous_science",
        "science_compatibility_proven",
    }
    need(type(proof) is dict and set(proof) == fields, "Exact parent capture fields required")
    roster = scope(proof.get("kind"))
    need(
        proof.get("schema") == 1
        and proof.get("format") == "wav_pgcopy_native_v2"
        and proof.get("scope_sha256") == SCOPE_SHA256
        and proof.get("complete") is True
        and proof.get("source_read_only") is True
        and proof.get("backup_anchor_reference_only") is True
        and proof.get("backup_snapshot_consistency_proven") is False
        and proof.get("cross_database_atomic") is False
        and proof.get("same_snapshot_as_previous_science") is False
        and proof.get("science_compatibility_proven") is False,
        "Exact parent-only capture contract required",
    )
    need(
        proof["metadata_sha256"] == SCOPE["metadata_sha256"]
        and re.fullmatch(r"[a-f0-9]{32}", proof["capture_id"])
        and all(
            type(proof[k]) is str and re.fullmatch(r"[a-f0-9]{64}", proof[k])
            for k in ("snapshot_sha256", "schema_master_sha256", "provider_scope_sha256")
        )
        and (
            proof["imported_snapshot_sha256"] is None
            or re.fullmatch(r"[a-f0-9]{64}", proof["imported_snapshot_sha256"])
        ),
        "Pinned parent capture/backup identities differ",
    )
    identity = proof["source_identity"]
    need(
        type(identity) is dict
        and all(identity.get(k) == v for k, v in roster["identity"].items())
        and identity.get("role")
        == identity.get("session_role")
        == "greenmind_archive_reader_33fe395"
        and identity.get("role_oid") == 499352
        and identity.get("role_unprivileged") is True
        and identity.get("read_only") == "on"
        and identity.get("in_recovery") is False,
        "Parent capture source identity differs",
    )
    begin, end = (datetime.fromisoformat(proof[k]) for k in ("started_at", "finished_at"))
    need(
        begin.tzinfo is not None
        and end.tzinfo is not None
        and 0 <= (end - begin).total_seconds() <= 95,
        "Parent capture time window differs",
    )
    files = proof.get("files")
    need(
        type(files) is list and len(files) == len(roster["tables"]),
        "Complete parent table roster required",
    )
    for entry, table in zip(files, roster["tables"], strict=True):
        need(
            type(entry) is dict
            and set(entry) == {"table", "file", "columns", "rows", "bytes", "sha256", "source_stat"}
            and entry["table"] == table["name"]
            and entry["columns"] == table["columns"]
            and entry["file"] == table["name"] + ".pgcopy"
            and type(entry["rows"]) is int
            and entry["rows"] >= 0
            and type(entry["bytes"]) is int
            and len(HEADER) + len(TRAILER) <= entry["bytes"] <= MAX_CAPTURE_BYTES
            and re.fullmatch(r"[a-f0-9]{64}", entry["sha256"]),
            "Parent capture table contract differs",
        )
        need(
            type(entry["source_stat"]) is list
            and len(entry["source_stat"]) == 5
            and all(type(x) is int and x >= 0 for x in entry["source_stat"])
            and entry["source_stat"][2] == entry["bytes"],
            "Parent source generation fields differ",
        )
    need(
        sum(f["bytes"] for f in files) == proof["total_bytes"] <= MAX_CAPTURE_BYTES,
        "Parent capture byte total differs",
    )


def prepare_index(source, capture_sha256, output, *, checkpoint):
    proof = load_capture(source, capture_sha256, verify_files=True, checkpoint=checkpoint)
    result = []
    for entry in proof["files"]:
        with (Path(source) / entry["file"]).open("rb") as stream:
            need(stream.read(len(HEADER)) == HEADER, "Parent COPY header differs")
            first = rows = payload = sequence = 0
            start = len(HEADER)
            h = hashlib.sha256(HEADER)
            advised = 0
            while True:
                checkpoint()
                offset = stream.tell()
                row = scientific_parts._row(stream, len(entry["columns"]))
                if hasattr(os, "posix_fadvise") and offset - advised >= 8 * 1024**2:
                    os.posix_fadvise(
                        stream.fileno(), advised, offset - advised, os.POSIX_FADV_DONTNEED
                    )
                    advised = offset
                if row is None:
                    need(not stream.read(1), "Parent COPY trailer differs")
                    result.append(
                        scientific_parts._part_entry(
                            entry["table"], sequence, first, rows, start, offset, payload, h
                        )
                    )
                    break
                need(
                    len(row) + len(HEADER) + len(TRAILER) <= MAX_PART_BYTES,
                    "Parent row exceeds part bound",
                )
                if rows and (
                    rows == MAX_PART_ROWS
                    or payload + len(row) + len(HEADER) + len(TRAILER) > MAX_PART_BYTES
                ):
                    result.append(
                        scientific_parts._part_entry(
                            entry["table"], sequence, first, rows, start, offset, payload, h
                        )
                    )
                    first += rows
                    sequence += 1
                    start = offset
                    rows = payload = 0
                    h = hashlib.sha256(HEADER)
                h.update(row)
                rows += 1
                payload += len(row)
            need(first + rows == entry["rows"], "Parent native row count differs")
    index = {
        "format": scientific_parts.PART_FORMAT,
        "capture_sha256": capture_sha256,
        "scope_sha256": SCOPE_SHA256,
        "parts": result,
    }
    validate_index(proof, index, capture_sha256)
    return {"index": index, "index_sha256": _save(Path(output), index)}


def validate_index(proof, index, capture_sha256):
    validate_capture(proof)
    need(
        type(index) is dict
        and set(index) == {"format", "capture_sha256", "scope_sha256", "parts"}
        and index.get("format") == scientific_parts.PART_FORMAT
        and index.get("capture_sha256") == capture_sha256
        and index.get("scope_sha256") == SCOPE_SHA256
        and type(index.get("parts")) is list
        and len(index["parts"]) <= 1024,
        "Exact bounded parent part index required",
    )
    position = 0
    for entry in proof["files"]:
        first = sequence = 0
        offset = len(HEADER)
        while (
            position < len(index["parts"]) and index["parts"][position]["table"] == entry["table"]
        ):
            p = index["parts"][position]
            need(
                type(p) is dict
                and set(p)
                == {"table", "sequence", "first_row", "rows", "start", "end", "bytes", "sha256"}
                and all(
                    type(p[k]) is int
                    for k in ("sequence", "first_row", "rows", "start", "end", "bytes")
                )
                and p["sequence"] == sequence
                and p["first_row"] == first
                and p["start"] == offset
                and type(p["rows"]) is int
                and 0 <= p["rows"] <= MAX_PART_ROWS
                and p["end"] >= offset
                and p["bytes"] == p["end"] - offset + len(HEADER) + len(TRAILER)
                and p["bytes"] <= MAX_PART_BYTES
                and re.fullmatch(r"[a-f0-9]{64}", p["sha256"])
                and (p["rows"] > 0 or entry["rows"] == 0 and sequence == 0),
                "Parent part coverage differs",
            )
            first += p["rows"]
            offset = p["end"]
            sequence += 1
            position += 1
        need(
            sequence > 0 and first == entry["rows"] and offset == entry["bytes"] - len(TRAILER),
            "Parent table part coverage is incomplete",
        )
    need(position == len(index["parts"]), "Unexpected parent table in index")


def materialize_part(source, capture_sha256, index, position, output, *, checkpoint):
    proof = load_capture(source, capture_sha256, checkpoint=checkpoint)
    validate_index(proof, index, capture_sha256)
    need(
        type(position) is int and 0 <= position < len(index["parts"]),
        "Exact parent part position required",
    )
    part = index["parts"][position]
    entry = next(f for f in proof["files"] if f["table"] == part["table"])
    output = Path(output)
    need(not os.path.lexists(output), "New parent part output required")
    with (Path(source) / entry["file"]).open("rb") as stream, output.open("xb") as body:
        os.fchmod(body.fileno(), 0o600)
        body.write(HEADER)
        stream.seek(part["start"])
        remaining = part["end"] - part["start"]
        while remaining:
            checkpoint()
            block = stream.read(min(65536, remaining))
            need(bool(block), "Parent source part truncated")
            body.write(block)
            remaining -= len(block)
        body.write(TRAILER)
        body.flush()
        os.fsync(body.fileno())
        os.fchmod(body.fileno(), 0o400)
    need(
        output.stat().st_size == part["bytes"]
        and checksum(output) == part["sha256"]
        and file_identity(Path(source) / entry["file"]) == entry["source_stat"],
        "Parent part native digest differs",
    )
    return part
