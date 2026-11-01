"""Native V3 part comparison in a separately admitted ephemeral PostgreSQL target.

No container is started here. The caller must independently pin and continuously
check its isolated instance, cgroups, volume, health, lease and source guards.
"""

import fcntl
import hashlib
import json
import os
import re
import stat
import struct

from sqlalchemy import text

from app.tiered_archive import backup, storage

from . import scientific_parts as parts
from . import scientific_v3 as v3
from .policy import ArchiveBlocked, checksum
from .scientific_capture import HEADER, digest_json
from .scientific_scope_v3 import SOURCE_SCOPE, SOURCE_SCOPE_SHA256

_ALLOWED_TYPES = {
    "bigint",
    "boolean",
    "character varying(20)",
    "character varying(50)",
    "character varying(500)",
    "character varying(64)",
    "double precision",
    "integer",
    "json",
    "timestamp with time zone",
    "uuid",
}


def _rows(path, columns, expected_rows, checkpoint):
    """Hash every native row by UUID PK; at most 20,000 one-row hashes retained."""
    v3._typed(path, columns, expected_rows, checkpoint)
    result = {}
    with path.open("rb") as body:
        v3._need(body.read(len(HEADER)) == HEADER, "Restore COPY header differs")
        while True:
            checkpoint()
            row = parts._row(body, len(columns))
            if row is None:
                v3._need(not body.read(1), "Restore COPY trailer differs")
                break
            v3._need(struct.unpack("!i", row[2:6])[0] == 16, "Restore UUID PK differs")
            primary = row[6:22]
            v3._need(primary not in result, "Duplicate native part primary key")
            result[primary] = hashlib.sha256(row).digest()
    v3._need(len(result) == expected_rows <= parts.MAX_PART_ROWS, "Restore part row total differs")
    return result


class _Sink:
    def __init__(self, output, limit, checkpoint):
        self.output, self.limit, self.checkpoint = output, limit, checkpoint
        self.count = 0

    def write(self, block):
        self.checkpoint()
        self.count += len(block)
        v3._need(self.count <= self.limit, "Restored native output exceeds part budget")
        return self.output.write(block)


class _Reader:
    def __init__(self, source, checkpoint):
        self.source, self.checkpoint = source, checkpoint

    def read(self, amount=-1):
        self.checkpoint()
        v3._need(0 <= amount <= 1024 * 1024, "Unbounded COPY read refused")
        return self.source.read(amount)


def _target(db, engine, target):
    url = engine.url
    required = {
        "database",
        "database_oid",
        "role",
        "role_oid",
        "port",
        "server_address",
        "postmaster_start",
    }
    v3._need(type(target) is dict and set(target) == required, "Pinned restore identity required")
    v3._need(
        isinstance(target["database"], str)
        and re.fullmatch(r"science_v3_[a-f0-9]{32}", target["database"])
        and target["role"] == target["database"]
        and type(target["database_oid"]) is int
        and target["database_oid"] > 0
        and type(target["role_oid"]) is int
        and target["role_oid"] > 0
        and type(target["port"]) is int
        and 1024 <= target["port"] <= 65535
        and target["port"] not in (5432, 5433)
        and isinstance(target["server_address"], str)
        and isinstance(target["postmaster_start"], str)
        and url.drivername == "postgresql+psycopg2"
        and url.host == "127.0.0.1"
        and url.port == target["port"]
        and url.database == target["database"]
        and url.username == target["role"]
        and bool(url.password)
        and not url.query,
        "Dedicated loopback science target required",
    )
    actual = dict(
        db.execute(
            text("""SELECT current_database() AS database,
      (SELECT oid::bigint FROM pg_database WHERE datname=current_database()) AS database_oid,
      (SELECT datdba::bigint FROM pg_database WHERE datname=current_database()) AS database_owner_oid,
      current_user AS role, session_user AS session_role,
      (SELECT oid::bigint FROM pg_roles WHERE rolname=current_user) AS role_oid,
      (SELECT rolcanlogin AND NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)
       FROM pg_roles WHERE rolname=current_user) AS unprivileged,
      current_setting('server_version_num')::int AS version,
      pg_is_in_recovery() AS in_recovery, inet_server_addr()::text AS server_address,
      inet_server_port() AS port, pg_postmaster_start_time()::text AS postmaster_start""")
        )
        .mappings()
        .one()
    )
    v3._need(
        all(actual[k] == target[k] for k in required)
        and actual["database_owner_oid"] == target["role_oid"]
        and actual["role"] == actual["session_role"]
        and actual["unprivileged"] is True
        and actual["version"] == 150010
        and actual["in_recovery"] is False,
        "Restore target identity changed",
    )
    return actual


def _restore_part(
    whole_reference,
    whole_sha256,
    position,
    key,
    verifier,
    engine,
    target,
    workdir,
    ledger,
    *,
    checkpoint,
    target_guard,
):
    """Read/authenticate whole and part, then compare all typed rows in a temp table.

    The input whole reference must be the locally journalled verified object. Target
    admission and independently read-only verifier identity remain caller duties.
    """
    v3._need(
        callable(checkpoint) and callable(target_guard) and not ledger.read_only,
        "Independent restore and resource guards required",
    )
    v3._archive_scope(ledger)
    v3._need(type(position) is int and 0 <= position <= 1000000, "Invalid part position")
    v3._need(type(key) is bytes and len(key) in (16, 24, 32), "Valid AES key required")
    v3._need(
        type(whole_sha256) is str and re.fullmatch(r"[a-f0-9]{64}", whole_sha256),
        "Pinned whole manifest digest required",
    )
    v3._need(
        type(whole_reference) is dict
        and set(whole_reference) == {"format", "sha256", "bytes", "plaintext_sha256", "key"}
        and whole_reference["format"] == "bin"
        and whole_reference["plaintext_sha256"] == whole_sha256
        and type(whole_reference["bytes"]) is int
        and 0 < whole_reference["bytes"] <= 17 * 1024**2
        and isinstance(whole_reference["sha256"], str)
        and re.fullmatch(r"[a-f0-9]{64}", whole_reference["sha256"]),
        "Invalid V3 whole reference",
    )
    storage.validate_key(whole_reference["key"])
    v3._need(
        whole_reference["key"].endswith("/" + whole_reference["sha256"] + ".bin"),
        "V3 whole object key differs",
    )
    dbj = ledger.db
    v3._need(not dbj.in_transaction, "Dedicated idle V3 journal required")
    token = hashlib.sha256(("whole:" + whole_sha256).encode()).hexdigest()
    record = dbj.execute(
        "SELECT body_sha256,state,reference FROM science_v3_whole WHERE token=?", (token,)
    ).fetchone()
    v3._need(
        record is not None and record == (whole_sha256, "verified", v3._canonical(whole_reference)),
        "Verified journalled whole inventory required",
    )
    workdir = v3._directory(workdir)
    stem = workdir / ("restore-" + token + "-" + str(position))
    paths = [stem.with_suffix(s) for s in (".whole.enc", ".whole", ".part.enc", ".part", ".out")]
    # Prior interrupted local reads are job-private derivatives, not originals.
    for position_in_workset, path in enumerate(paths):
        if os.path.lexists(path):
            info = path.lstat()
            v3._need(
                stat.S_ISREG(info.st_mode)
                and info.st_uid == os.geteuid()
                and info.st_nlink == 1
                and stat.S_IMODE(info.st_mode)
                in ((0o600, 0o644) if position_in_workset in (0, 2) else (0o600,)),
                "Unsafe interrupted restore work file",
            )
            path.unlink()

    storage.disk_reserve(workdir, 192 * 1024**2)
    checkpoint()
    target_guard()
    verifier.download(whole_reference["key"], paths[0], whole_reference["bytes"])
    v3._need(
        paths[0].stat().st_size == whole_reference["bytes"]
        and checksum(paths[0]) == whole_reference["sha256"],
        "Whole readback differs",
    )
    backup.decrypt(paths[0], paths[1], key, whole_sha256, checkpoint=checkpoint)
    v3._need(paths[1].stat().st_size <= 16 * 1024**2, "Whole manifest exceeds budget")
    whole = json.loads(paths[1].read_bytes())
    v3._need(
        type(whole) is dict
        and set(whole)
        == {
            "format",
            "capture",
            "capture_sha256",
            "index",
            "index_sha256",
            "destination",
            "references",
            "native_restore_verified",
            "parent_fk_closure_proven",
            "cross_database_atomic",
        }
        and whole["format"] == "greenmind.science-whole.v3"
        and whole["destination"] == verifier.identity
        and whole["native_restore_verified"] is False
        and whole["parent_fk_closure_proven"] is False
        and whole["cross_database_atomic"] is False,
        "Whole manifest contract differs",
    )
    proof, index = whole["capture"], whole["index"]
    v3._archive_scope(
        ledger, whole["capture_sha256"], whole["index_sha256"], proof.get("capture_id")
    )
    v3._need(
        type(proof) is dict
        and proof.get("scope") == SOURCE_SCOPE
        and proof.get("scope_sha256") == SOURCE_SCOPE_SHA256
        and digest_json(proof["scope"]) == SOURCE_SCOPE_SHA256
        and proof.get("complete") is True
        and proof.get("source") == "gateway",
        "Whole source scope differs",
    )
    parts.validate_index(proof, index, whole["capture_sha256"])
    v3._need(
        type(position) is int
        and 0 <= position < len(index["parts"])
        and type(whole["references"]) is list
        and len(whole["references"]) == len(index["parts"]),
        "Whole part roster differs",
    )
    part, ref = index["parts"][position], whole["references"][position]
    v3._need(
        type(ref) is dict
        and set(ref) == {"format", "sha256", "bytes", "plaintext_sha256", "key"}
        and ref["format"] == "bin"
        and ref["plaintext_sha256"] == part["sha256"]
        and type(ref["bytes"]) is int
        and 0 < ref["bytes"] <= storage.MAX_BYTES
        and isinstance(ref["sha256"], str)
        and re.fullmatch(r"[a-f0-9]{64}", ref["sha256"])
        and ref["key"].endswith("/" + ref["sha256"] + ".bin"),
        "Native part reference differs",
    )
    storage.validate_key(ref["key"])
    checkpoint()
    verifier.download(ref["key"], paths[2], ref["bytes"])
    v3._need(
        paths[2].stat().st_size == ref["bytes"] and checksum(paths[2]) == ref["sha256"],
        "Native part ciphertext readback differs",
    )
    backup.decrypt(paths[2], paths[3], key, part["sha256"], checkpoint=checkpoint)
    v3._need(paths[3].stat().st_size == part["bytes"], "Native part size differs")
    table = next(x for x in proof["files"] if x["table"] == part["table"])
    columns = table["columns"]
    v3._need(
        columns == [c for c in SOURCE_SCOPE["columns"] if c["table_name"] == part["table"]]
        and all(
            c["formatted_type"] in _ALLOWED_TYPES and re.fullmatch(r"[a-z_][a-z0-9_]*", c["name"])
            for c in columns
        )
        and columns[0]["name"] == "id",
        "Exact allowed native columns and types required",
    )
    original = _rows(paths[3], columns, part["rows"], checkpoint)
    fields = ",".join('"' + c["name"] + '"' for c in columns)
    definitions = ",".join(
        '"' + c["name"] + '" ' + c["formatted_type"] + (" NOT NULL" if c["not_null"] else "")
        for c in columns
    )
    stage_name = (
        "gm_science_v3_"
        + hashlib.sha256((whole_sha256 + ":" + str(position)).encode()).hexdigest()[:32]
    )
    relation = 'public."' + stage_name + '"'
    expected_columns = [
        {key: c[key] for key in ("name", "formatted_type", "not_null", "type_oid")} for c in columns
    ]

    def begin_checked(db):
        db.execute(text("SET LOCAL statement_timeout='8000ms'"))
        db.execute(text("SET LOCAL lock_timeout='150ms'"))
        db.execute(text("SET LOCAL search_path=pg_catalog"))
        target_guard()
        checkpoint()
        return _target(db, engine, target)

    target_guard()
    with engine.connect() as db:
        try:
            identity = begin_checked(db)
            db.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS "
                    + relation
                    + " ("
                    + definitions
                    + ', PRIMARY KEY ("id"))'
                )
            )
            actual_columns = list(
                db.execute(
                    text("""SELECT attname AS name,
                format_type(atttypid,atttypmod) AS formatted_type, attnotnull AS not_null,
                atttypid::bigint AS type_oid FROM pg_attribute
                WHERE attrelid=to_regclass(:relation) AND attnum>0 AND NOT attisdropped
                ORDER BY attnum"""),
                    {"relation": "public." + stage_name},
                ).mappings()
            )
            v3._need(
                [dict(item) for item in actual_columns] == expected_columns,
                "Restored SQL type or column roster differs",
            )
            shape = db.execute(
                text("""SELECT c.relkind, c.relpersistence, c.relowner::bigint,
                NOT c.relrowsecurity AND NOT c.relforcerowsecurity,
                NOT EXISTS (SELECT 1 FROM pg_trigger t WHERE t.tgrelid=c.oid AND NOT t.tgisinternal),
                NOT EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhrelid=c.oid OR i.inhparent=c.oid),
                (SELECT count(*) FROM pg_constraint k WHERE k.conrelid=c.oid),
                (SELECT count(*) FROM pg_constraint k WHERE k.conrelid=c.oid AND k.contype='p'
                 AND pg_get_constraintdef(k.oid)='PRIMARY KEY (id)')
                FROM pg_class c WHERE c.oid=to_regclass(:relation)"""),
                {"relation": "public." + stage_name},
            ).one()
            v3._need(
                tuple(shape) == ("r", "p", target["role_oid"], True, True, True, 1, 1),
                "Untrusted native staging table",
            )
            db.commit()  # The empty own stage survives interrupted COPY.

            v3._need(begin_checked(db) == identity, "Restore target changed")
            db.execute(text("LOCK TABLE " + relation + " IN ACCESS EXCLUSIVE MODE"))
            count = db.execute(text("SELECT count(*) FROM " + relation)).scalar_one()
            v3._need(count in (0, part["rows"]), "Interrupted staging rows differ")
            if count == 0 and part["rows"]:
                cursor = db.connection.driver_connection.cursor()
                try:
                    with paths[3].open("rb") as src:
                        cursor.copy_expert(
                            "COPY " + relation + " (" + fields + ") FROM STDIN (FORMAT binary)",
                            _Reader(src, checkpoint),
                        )
                finally:
                    cursor.close()
            db.commit()  # Complete loaded stage survives failed readback or process exit.

            v3._need(begin_checked(db) == identity, "Restore target changed")
            db.execute(text("LOCK TABLE " + relation + " IN ACCESS EXCLUSIVE MODE"))
            cursor = db.connection.driver_connection.cursor()
            try:
                with paths[4].open("xb") as output:
                    os.chmod(paths[4], 0o600)
                    cursor.copy_expert(
                        "COPY (SELECT "
                        + fields
                        + " FROM "
                        + relation
                        + ") TO STDOUT (FORMAT binary)",
                        _Sink(output, parts.MAX_PART_BYTES, checkpoint),
                    )
                    output.flush()
                    os.fsync(output.fileno())
            finally:
                cursor.close()
            returned = _rows(paths[4], columns, part["rows"], checkpoint)
            v3._need(original == returned, "Native restored PK or typed values differ")
            target_guard()
            v3._need(_target(db, engine, target) == identity, "Restore target changed")
            checkpoint()
            audit = {
                "format": "greenmind.science-part-restore.v3",
                "whole_sha256": whole_sha256,
                "capture_id": proof["capture_id"],
                "index_sha256": whole["index_sha256"],
                "position": position,
                "part_sha256": part["sha256"],
                "rows": part["rows"],
                "target": target,
                "native_row_multiset_equal": True,
                "parent_fk_closure_proven": False,
                "writer_allowed": False,
            }
            dbj.execute("""CREATE TABLE IF NOT EXISTS science_v3_restore (
                whole_sha256 TEXT NOT NULL, position INTEGER NOT NULL, audit TEXT NOT NULL,
                PRIMARY KEY(whole_sha256,position))""")
            dbj.commit()
            prior = dbj.execute(
                "SELECT audit FROM science_v3_restore WHERE whole_sha256=? AND position=?",
                (whole_sha256, position),
            ).fetchone()
            v3._need(
                prior is None or prior[0] == v3._canonical(audit), "Restore audit binding changed"
            )
            with dbj:
                dbj.execute(
                    "INSERT OR IGNORE INTO science_v3_restore VALUES (?,?,?)",
                    (whole_sha256, position, v3._canonical(audit)),
                )
            # The durable part audit precedes any removal of the own staging table.
            db.execute(text("DROP TABLE " + relation))
            db.commit()
        except BaseException:
            db.rollback()
            raise
    for path in paths:
        v3._need(path.is_file() and not path.is_symlink(), "Unsafe restore work file")
        path.unlink()
    v3._sync_dir(workdir)
    return audit


def restore_part(
    whole_reference,
    whole_sha256,
    position,
    key,
    verifier,
    engine,
    target,
    workdir,
    ledger,
    *,
    checkpoint,
    target_guard,
):
    """Serialize the workset across publishing and native part restores."""
    workdir = v3._directory(workdir)
    fd = os.open(workdir / "science-v3.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ArchiveBlocked("V3 work directory busy") from exc
        return _restore_part(
            whole_reference,
            whole_sha256,
            position,
            key,
            verifier,
            engine,
            target,
            workdir,
            ledger,
            checkpoint=checkpoint,
            target_guard=target_guard,
        )
    finally:
        os.close(fd)
