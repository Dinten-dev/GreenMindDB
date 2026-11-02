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
import uuid

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


def _target(db, engine, target, *, application=False):
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
        and re.fullmatch(
            r"gm_native_restore_[a-f0-9]{12,32}_gateway"
            if application
            else r"science_v3_[a-f0-9]{32}",
            target["database"],
        )
        and (application or target["role"] == target["database"])
        and (not application or target["port"] == 19442)
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
    application=None,
    finalize=False,
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
    if application is not None:
        audit = _application_part(
            db_engine=engine,
            target=target,
            contract=application,
            whole=whole,
            whole_sha256=whole_sha256,
            position=position,
            reference=ref,
            native=paths[3],
            output=paths[4],
            original=original,
            checkpoint=checkpoint,
            target_guard=target_guard,
            finalize=finalize,
        )
        for path in paths[:4]:
            path.unlink()
        if paths[4].exists():
            paths[4].unlink()
        v3._sync_dir(workdir)
        return audit
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
    application=None,
    finalize=False,
):
    """Serialize publishing/restoration worksets; default remains one temp part.

    application selects actual-table loading into the caller's existing isolated
    target. finalize=True replays position zero and audits the complete target.
    target_guard must pin the actual container/image, PID generation and resource
    controls, including a Source-host target or an authenticated SSH forwarding.
    AES decryption and these files must remain on Archive, never on the Mac.
    """
    v3._need(
        type(finalize) is bool and (not finalize or application is not None),
        "Final audit requires actual application mode",
    )
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
            application=application,
            finalize=finalize,
        )
    finally:
        os.close(fd)


APPLICATION_FORMAT = "greenmind.science-application-restore.v3"
_SCIENCE_TABLES = ("wav_feature", "wav_feature_version")
_JOURNALS = ("greenmind_science_restore", "greenmind_science_restore_part")


def _application_contract(contract, whole, whole_sha256, target):
    """An independently pinned plan supplies scope; live SQL supplies acceptance."""
    from app.tiered_archive import codec, restore_session

    fields = {
        "format",
        "marker_sha256",
        "plan",
        "plan_sha256",
        "masters_sha256",
        "expected_parts",
        "expected_rows",
        "sessions",
    }
    v3._need(
        type(contract) is dict
        and set(contract) == fields
        and contract["format"] == APPLICATION_FORMAT,
        "Exact application restore contract required",
    )
    for field in ("marker_sha256", "plan_sha256", "masters_sha256"):
        v3._need(
            restore_session.sha(contract[field]) and contract[field] != "0" * 64,
            "Pinned application restore evidence required",
        )
    restore_session.validate_plan(contract["plan"], contract["plan_sha256"])
    plan = contract["plan"]
    v3._need(
        plan["source"] == "gateway"
        and plan["dataset"] in _SCIENCE_TABLES
        and plan["environment"] == "production"
        and all(plan["universe"]["tables"].get(t) == "data" for t in _SCIENCE_TABLES)
        and all(
            plan["universe"]["tables"].get(t) == "master"
            for t in ("wav_file", "sensor", "gateway", "zone", "organization")
        ),
        "Actual scientific parent and master universe required",
    )
    v3._need(
        type(contract["expected_parts"]) is int
        and contract["expected_parts"] == len(whole["index"]["parts"])
        and type(contract["expected_rows"]) is int
        and contract["expected_rows"] == sum(f["rows"] for f in whole["capture"]["files"])
        and type(contract["sessions"]) is dict
        and len(contract["sessions"]) <= 128
        and all(
            restore_session.sha(k) and restore_session.sha(v)
            for k, v in contract["sessions"].items()
        ),
        "Complete science totals and universe session roster required",
    )
    return codec.digest(
        {
            "contract": contract,
            "whole_sha256": whole_sha256,
            "target": target,
            "destination": whole["destination"],
        }
    )


def _application_identity(db, engine, target, contract, target_guard, checkpoint):
    from app.tiered_archive.recovery import restore_url

    restore_url(engine.url)
    checkpoint()
    target_guard()
    actual = _target(db, engine, target, application=True)
    marker = db.execute(
        text("""SELECT encode(sha256(convert_to(coalesce(
        pg_catalog.shobj_description(oid,'pg_database'),''),'UTF8')),'hex'),
        has_database_privilege('public',current_database(),'CONNECT'),
        has_database_privilege('public',current_database(),'TEMPORARY'),
        current_setting('session_replication_role')
        FROM pg_database WHERE datname=current_database()""")
    ).one()
    v3._need(
        tuple(marker) == (contract["marker_sha256"], False, False, "origin"),
        "Isolated application target marker or access changed",
    )
    v3._need(actual["port"] == 19442, "Application restore port differs")


def _application_schema(db, target):
    """Require real source columns, every unique/index, and enabled real FKs."""
    for name in _SCIENCE_TABLES:
        columns = list(
            db.execute(
                text("""SELECT a.attname AS name,
            format_type(a.atttypid,a.atttypmod) AS formatted_type,
            a.attnotnull AS not_null, a.atttypid::bigint AS type_oid,
            a.attidentity AS identity_kind, a.attgenerated AS generated_kind,
            pg_get_expr(d.adbin,d.adrelid) AS default_expression
            FROM pg_attribute a LEFT JOIN pg_attrdef d
              ON d.adrelid=a.attrelid AND d.adnum=a.attnum
            WHERE a.attrelid=to_regclass(:relation) AND a.attnum>0
              AND NOT a.attisdropped ORDER BY a.attnum"""),
                {"relation": "public." + name},
            ).mappings()
        )
        keys = (
            "name",
            "formatted_type",
            "not_null",
            "type_oid",
            "identity_kind",
            "generated_kind",
            "default_expression",
        )
        expected = [
            {k: c[k] for k in keys} for c in SOURCE_SCOPE["columns"] if c["table_name"] == name
        ]
        v3._need(
            [dict(x) for x in columns] == expected, "Actual application column contract differs"
        )
        shape = db.execute(
            text("""SELECT c.relkind,c.relpersistence,c.relowner::bigint,
            NOT c.relrowsecurity AND NOT c.relforcerowsecurity,
            NOT EXISTS(SELECT 1 FROM pg_inherits i
              WHERE i.inhrelid=c.oid OR i.inhparent=c.oid)
            FROM pg_class c WHERE c.oid=to_regclass(:relation)"""),
            {"relation": "public." + name},
        ).one_or_none()
        v3._need(
            shape is not None and tuple(shape) == ("r", "p", target["role_oid"], True, True),
            "Actual application relation owner or shape differs",
        )
        constraints = list(
            db.execute(
                text("""SELECT k.conname AS name,k.contype::text AS type,
            pg_get_constraintdef(k.oid) AS definition,k.condeferrable AS deferrable,
            k.condeferred AS deferred,k.convalidated AS validated
            FROM pg_constraint k WHERE k.conrelid=to_regclass(:relation)
            ORDER BY k.conname"""),
                {"relation": "public." + name},
            ).mappings()
        )
        keys = ("name", "type", "definition", "deferrable", "deferred", "validated")
        expected = sorted(
            (
                {k: c[k] for k in keys}
                for c in SOURCE_SCOPE["constraints"]
                if c["table_name"] == name
            ),
            key=lambda x: x["name"],
        )
        v3._need(
            [dict(x) for x in constraints] == expected,
            "Actual application unique or parent FK contract differs",
        )
        indexes = list(
            db.execute(
                text("""SELECT ci.relname AS name,
            pg_get_indexdef(i.indexrelid) AS definition, i.indisprimary AS primary_index,
            i.indisunique AS unique_index,i.indisready AS ready,i.indisvalid AS valid
            FROM pg_index i JOIN pg_class ci ON ci.oid=i.indexrelid
            WHERE i.indrelid=to_regclass(:relation) ORDER BY ci.relname"""),
                {"relation": "public." + name},
            ).mappings()
        )
        keys = ("name", "definition", "primary_index", "unique_index", "ready", "valid")
        expected = sorted(
            ({k: c[k] for k in keys} for c in SOURCE_SCOPE["indexes"] if c["table_name"] == name),
            key=lambda x: x["name"],
        )
        v3._need(
            [dict(x) for x in indexes] == expected, "Actual application index contract differs"
        )
        # A validated FK whose enforcement triggers are disabled is insufficient.
        bad = db.execute(
            text("""SELECT EXISTS(SELECT 1 FROM pg_trigger t
            JOIN pg_constraint k ON k.oid=t.tgconstraint
            WHERE k.conrelid=to_regclass(:relation) AND k.contype='f'
              AND t.tgenabled NOT IN ('O','A'))"""),
            {"relation": "public." + name},
        ).scalar_one()
        v3._need(bad is False, "Application parent FK enforcement is disabled")


def _application_journals(db, target, binding, *, create=True):
    """Exact reserved names; never adopt an unrelated table or search_path alias."""
    from sqlalchemy import Column, Integer, LargeBinary, MetaData, String, Table, Text

    metadata = MetaData(schema="public")
    state = Table(
        _JOURNALS[0],
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=False),
        Column("binding", String(64), nullable=False),
        Column("audit", Text),
    )
    progress = Table(
        _JOURNALS[1],
        metadata,
        Column("position", Integer, primary_key=True, autoincrement=False),
        Column("binding", String(64), nullable=False),
        Column("keys", LargeBinary, nullable=False),
    )
    for table in (state, progress):
        present = db.execute(
            text("SELECT to_regclass(:name) IS NOT NULL"), {"name": "public." + table.name}
        ).scalar_one()
        marker = APPLICATION_FORMAT + ":" + binding + ":" + table.name
        if not present:
            v3._need(create, "Incomplete reserved science journal set")
            table.create(db)
            db.execute(
                text('COMMENT ON TABLE public."' + table.name + '" IS :marker'), {"marker": marker}
            )
        info = db.execute(
            text("""SELECT c.relkind,c.relpersistence,c.relowner::bigint,
            NOT c.relrowsecurity AND NOT c.relforcerowsecurity,
            obj_description(c.oid,'pg_class'),
            NOT EXISTS(SELECT 1 FROM pg_trigger t WHERE t.tgrelid=c.oid),
            NOT EXISTS(SELECT 1 FROM pg_inherits i
              WHERE i.inhrelid=c.oid OR i.inhparent=c.oid),
            (SELECT count(*) FROM pg_constraint k WHERE k.conrelid=c.oid),
            (SELECT ARRAY(SELECT a.attname::text
              FROM unnest(k.conkey) WITH ORDINALITY AS key(attnum,n)
              JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=key.attnum
              ORDER BY key.n) FROM pg_constraint k
              WHERE k.conrelid=c.oid AND k.contype='p'),
            NOT EXISTS(SELECT 1 FROM pg_constraint k WHERE k.conrelid=c.oid
              AND (k.condeferrable OR k.condeferred OR NOT k.convalidated))
            FROM pg_class c WHERE c.oid=to_regclass(:name)"""),
            {"name": "public." + table.name},
        ).one_or_none()
        pk = "id" if table is state else "position"
        expected_identity = ("r", "p", target["role_oid"], True, marker, True, True, 1, [pk], True)
        labels = (
            "relation_kind",
            "persistence",
            "owner",
            "no_rls",
            "marker",
            "no_triggers",
            "no_inheritance",
            "constraint_count",
            "pk_columns",
            "constraints_immediate_validated",
        )
        shape_matches = info is not None and len(info) == len(expected_identity)
        values = tuple(info) if shape_matches else (None,) * len(labels)
        checks = {
            label: shape_matches and actual == expected
            for label, actual, expected in zip(labels, values, expected_identity, strict=True)
        }
        if not all(checks.values()):
            error = ArchiveBlocked("Reserved science journal identity differs")
            error.safe_catalog_checks = checks
            error.safe_relation_name = table.name
            raise error
        actual = list(
            db.execute(
                text("""SELECT a.attname,format_type(a.atttypid,a.atttypmod),
            a.attnotnull,a.attidentity,a.attgenerated,pg_get_expr(d.adbin,d.adrelid)
            FROM pg_attribute a LEFT JOIN pg_attrdef d
              ON d.adrelid=a.attrelid AND d.adnum=a.attnum
            WHERE a.attrelid=to_regclass(:name)
            AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum"""),
                {"name": "public." + table.name},
            )
        )
        expected = [(pk, "integer", True), ("binding", "character varying(64)", True)]
        if table is progress:
            expected.append(("keys", "bytea", True))
        else:
            expected.append(("audit", "text", False))
        expected = [(*row, "", "", None) for row in expected]
        v3._need([tuple(x) for x in actual] == expected, "Reserved science journal schema differs")
        db.execute(text('LOCK TABLE public."' + table.name + '" IN ACCESS EXCLUSIVE MODE'))
    return state, progress


def validate_application_journals(db):
    """Universe exclusion is conditional on the exact owned journal contract."""
    present = tuple(
        db.execute(
            text("SELECT to_regclass(:name) IS NOT NULL"), {"name": "public." + name}
        ).scalar_one()
        for name in _JOURNALS
    )
    if not any(present):
        return
    v3._need(all(present), "Incomplete reserved science journal set")
    info = db.execute(
        text("""SELECT current_database(),inet_server_port(),
        current_setting('server_version_num')::int,
        (SELECT oid::bigint FROM pg_roles WHERE rolname=current_user),
        (SELECT datdba::bigint FROM pg_database WHERE datname=current_database()),
        (SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls
          FROM pg_roles WHERE rolname=current_user)""")
    ).one()
    v3._need(
        re.fullmatch(r"gm_native_restore_[a-f0-9]{12,32}_gateway", info[0])
        and info[1] == 19442
        and info[2] == 150010
        and info[3] == info[4]
        and info[5] is False,
        "Reserved science journal target differs",
    )
    comment = db.execute(
        text("SELECT obj_description(to_regclass(:name),'pg_class')"),
        {"name": "public." + _JOURNALS[0]},
    ).scalar_one()
    prefix = APPLICATION_FORMAT + ":"
    suffix = ":" + _JOURNALS[0]
    v3._need(
        isinstance(comment, str) and comment.startswith(prefix) and comment.endswith(suffix),
        "Reserved science journal marker differs",
    )
    binding = comment[len(prefix) : -len(suffix)]
    v3._need(
        re.fullmatch(r"[a-f0-9]{64}", binding) is not None,
        "Reserved science journal binding differs",
    )
    state, _ = _application_journals(db, {"role_oid": info[3]}, binding, create=False)
    from sqlalchemy import select

    rows = list(db.execute(select(state.c.id, state.c.binding)).mappings())
    v3._need(rows == [{"id": 1, "binding": binding}], "Reserved science journal capture differs")


def _application_context(db, contract, target, *, loading):
    from app.tiered_archive import restore_baseline as baseline
    from app.tiered_archive import restore_fence as fence
    from app.tiered_archive import restore_universe as universe

    plan = contract["plan"]
    universe.bind(db, plan)
    tables, masters = universe.tables(db, plan)
    for name in sorted(tables):
        db.execute(text('LOCK TABLE public."' + name + '" IN SHARE ROW EXCLUSIVE MODE'))
    before = fence.check(db)
    v3._need(
        (not loading or before["phase"] == "loading")
        and baseline.anchor(db, masters, before) == contract["masters_sha256"],
        "Actual restored parent/master baseline is incomplete or changed",
    )
    _application_schema(db, target)
    for table in _SCIENCE_TABLES:
        missing = db.execute(
            text(
                'SELECT EXISTS(SELECT 1 FROM public."'
                + table
                + '" c LEFT JOIN public.wav_file p ON p.id=c.wav_file_id WHERE p.id IS NULL)'
            )
        ).scalar_one()
        v3._need(missing is False, "Actual restored scientific parent is missing")
    return before


def _application_readback(
    db, table, columns, keys, output, expected, checkpoint, *, verify_rows=True
):
    """COPY in the capture's row order, not target heap or UUID sort order."""
    v3._need(
        type(keys) is bytes and len(keys) == expected * 16 and expected <= parts.MAX_PART_ROWS,
        "Bounded science PK roster required",
    )
    ids = [str(uuid.UUID(bytes=keys[i : i + 16])) for i in range(0, len(keys), 16)]
    fields = ",".join('t."' + c["name"] + '"' for c in columns)
    cursor = db.connection.driver_connection.cursor()
    try:
        query = cursor.mogrify(
            "COPY (SELECT " + fields + " FROM unnest(%s::uuid[]) WITH ORDINALITY k(id,n) "
            'JOIN public."' + table + '" t ON t.id=k.id ORDER BY k.n) TO STDOUT (FORMAT binary)',
            (ids,),
        )
        with output.open("wb") as stream:
            os.chmod(output, 0o600)
            cursor.copy_expert(query, _Sink(stream, parts.MAX_PART_BYTES, checkpoint))
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        cursor.close()
    return _rows(output, columns, expected, checkpoint) if verify_rows else None


def _application_part(
    *,
    db_engine,
    target,
    contract,
    whole,
    whole_sha256,
    position,
    reference,
    native,
    output,
    original,
    checkpoint,
    target_guard,
    finalize=False,
):
    from sqlalchemy import select

    from app.tiered_archive import codec, restore_session
    from app.tiered_archive import restore_fence as fence

    binding = _application_contract(contract, whole, whole_sha256, target)
    part = whole["index"]["parts"][position]
    part_binding = codec.digest(
        {"root": binding, "position": position, "part": part, "reference": reference}
    )
    with db_engine.connect() as db, db.begin():
        db.execute(text("SET LOCAL statement_timeout='8000ms'"))
        db.execute(text("SET LOCAL lock_timeout='150ms'"))
        db.execute(text("SET LOCAL search_path=public,pg_catalog"))
        _application_identity(db, db_engine, target, contract, target_guard, checkpoint)
        # Database-local serialization survives different Archive work directories.
        v3._need(
            db.execute(text("SELECT pg_try_advisory_xact_lock(1735157614,19442)")).scalar_one()
            is True,
            "Application science target busy",
        )
        state, progress = _application_journals(db, target, binding)
        states = list(db.execute(select(state.c.id, state.c.binding)).mappings())
        v3._need(
            not states or states == [{"id": 1, "binding": binding}],
            "Application target already belongs to another capture",
        )
        if not states:
            db.execute(state.insert().values(id=1, binding=binding))
        recorded = list(db.execute(select(progress).order_by(progress.c.position)).mappings())
        v3._need(
            [r["position"] for r in recorded] == list(range(len(recorded))),
            "Application science progress sequence differs",
        )
        before = _application_context(db, contract, target, loading=position >= len(recorded))
        for name in _SCIENCE_TABLES:
            count = db.execute(text('SELECT count(*) FROM public."' + name + '"')).scalar_one()
            expected = sum(
                p["rows"] for p in whole["index"]["parts"][: len(recorded)] if p["table"] == name
            )
            v3._need(count == expected, "Unjournaled application science rows")
        replay = position < len(recorded)
        v3._need(replay or position == len(recorded), "Missing preceding science part")
        if finalize:
            v3._need(position == 0 and replay, "Final audit requires replay of first science part")
            result = _application_final(
                db, contract, whole, whole_sha256, binding, target, recorded, output, checkpoint
            )
            prior = db.execute(select(state.c.audit).where(state.c.id == 1)).scalar_one()
            encoded = v3._canonical(result)
            v3._need(prior is None or prior == encoded, "Durable final science audit changed")
            db.execute(state.update().where(state.c.id == 1).values(audit=encoded))
            _application_identity(db, db_engine, target, contract, target_guard, checkpoint)
            return result
        keys = b"".join(original)
        if replay:
            prior = recorded[position]
            v3._need(
                prior["binding"] == part_binding and bytes(prior["keys"]) == keys,
                "Application science replay binding differs",
            )
        else:
            columns = next(
                f["columns"] for f in whole["capture"]["files"] if f["table"] == part["table"]
            )
            fields = ",".join('"' + c["name"] + '"' for c in columns)
            cursor = db.connection.driver_connection.cursor()
            try:
                with native.open("rb") as stream:
                    cursor.copy_expert(
                        'COPY public."'
                        + part["table"]
                        + '" ('
                        + fields
                        + ") FROM STDIN (FORMAT binary)",
                        _Reader(stream, checkpoint),
                    )
            finally:
                cursor.close()
            fence.accept(db, before, {part["table"]: part["rows"]})
        columns = next(
            f["columns"] for f in whole["capture"]["files"] if f["table"] == part["table"]
        )
        returned = _application_readback(
            db, part["table"], columns, keys, output, part["rows"], checkpoint
        )
        v3._need(
            returned == original and checksum(output) == part["sha256"],
            "Actual application native rows differ",
        )
        if not replay:
            db.execute(progress.insert().values(position=position, binding=part_binding, keys=keys))
            totals, total = restore_session.dataset_total(db, part["table"])
            previous = sum(
                p["rows"] for p in whole["index"]["parts"][:position] if p["table"] == part["table"]
            )
            v3._need(
                total is None or (total["rows"] == previous and total["children"] == {}),
                "Science dataset already belongs to another load",
            )
            if total is None:
                db.execute(
                    totals.insert().values(dataset=part["table"], rows=part["rows"], children={})
                )
            else:
                db.execute(
                    totals.update()
                    .where(totals.c.dataset == part["table"])
                    .values(rows=previous + part["rows"])
                )
        _application_identity(db, db_engine, target, contract, target_guard, checkpoint)
    return {
        "format": APPLICATION_FORMAT,
        "whole_sha256": whole_sha256,
        "target": target,
        "position": position,
        "part_sha256": part["sha256"],
        "rows": part["rows"],
        "replayed": replay,
        "actual_application_tables": True,
        "native_row_multiset_equal": True,
        "full_restore_verified": False,
        "parent_fk_closure_proven": False,
        "archive_accepted": False,
        "writer_allowed": False,
    }


def _application_final(
    db, contract, whole, whole_sha256, binding, target, recorded, output, checkpoint
):
    """Rebuild both original COPY digests from actual SQL, in capture order.

    All application tables remain locked for this transaction. Each output file is
    bounded to one part; no full capture or full-table PK roster is materialized.
    The caller supplies a real lease/deadline across the full audit. Insufficient
    native time or resources is failure, never partial acceptance.
    """
    from app.tiered_archive import codec, restore_session
    from app.tiered_archive import restore_fence as fence

    from .scientific_capture import TRAILER

    index = whole["index"]["parts"]
    v3._need(
        len(recorded) == len(index) == contract["expected_parts"],
        "Every science part must exist in the same target",
    )
    digests = {name: hashlib.sha256(HEADER) for name in _SCIENCE_TABLES}
    totals = dict.fromkeys(_SCIENCE_TABLES, 0)
    sizes = dict.fromkeys(_SCIENCE_TABLES, len(HEADER) + len(TRAILER))
    for position, (part, record) in enumerate(zip(index, recorded, strict=True)):
        checkpoint()
        expected_binding = codec.digest(
            {
                "root": binding,
                "position": position,
                "part": part,
                "reference": whole["references"][position],
            }
        )
        v3._need(
            record["position"] == position and record["binding"] == expected_binding,
            "Final science part or ciphertext binding changed",
        )
        source = next(f for f in whole["capture"]["files"] if f["table"] == part["table"])
        _application_readback(
            db,
            part["table"],
            source["columns"],
            bytes(record["keys"]),
            output,
            part["rows"],
            checkpoint,
            verify_rows=False,
        )
        v3._need(
            output.stat().st_size == part["bytes"] and checksum(output) == part["sha256"],
            "Final actual application part differs from archive",
        )
        remaining = part["bytes"] - len(HEADER) - len(TRAILER)
        with output.open("rb") as stream:
            v3._need(stream.read(len(HEADER)) == HEADER, "Final native header differs")
            while remaining:
                checkpoint()
                block = stream.read(min(65536, remaining))
                v3._need(bool(block), "Final native row stream truncated")
                digests[part["table"]].update(block)
                remaining -= len(block)
                sizes[part["table"]] += len(block)
            v3._need(stream.read() == TRAILER, "Final native trailer differs")
        totals[part["table"]] += part["rows"]
    table_proofs = {}
    for name in _SCIENCE_TABLES:
        digests[name].update(TRAILER)
        source = next(f for f in whole["capture"]["files"] if f["table"] == name)
        actual = db.execute(text('SELECT count(*) FROM public."' + name + '"')).scalar_one()
        v3._need(
            actual == totals[name] == source["rows"]
            and sizes[name] == source["bytes"]
            and digests[name].hexdigest() == source["sha256"],
            "Full actual table native digest or row total differs",
        )
        table_proofs[name] = {
            "rows": actual,
            "bytes": sizes[name],
            "native_sha256": digests[name].hexdigest(),
        }
    v3._need(sum(totals.values()) == contract["expected_rows"], "Full science total differs")
    # These are the existing restore-session SQL records, not supplied PASS flags.
    sessions = list(
        db.execute(
            text(
                "SELECT id,plan_sha256,dataset,target_sha256,state,"
                "rows,audit_rows,pages,audit_pages FROM public.greenmind_restore_session ORDER BY id"
            )
        ).mappings()
    )
    v3._need(
        {r["id"]: r["plan_sha256"] for r in sessions} == contract["sessions"]
        and all(
            r["state"] == "verified"
            and r["target_sha256"] == restore_session.target_identity(db)
            and r["rows"] == r["audit_rows"]
            and r["pages"] == r["audit_pages"]
            for r in sessions
        ),
        "Complete independently pinned universe sessions are not verified",
    )
    universe = contract["plan"]["universe"]["tables"]
    for name, role in universe.items():
        if role != "data" or name in _SCIENCE_TABLES:
            continue
        actual = db.execute(text('SELECT count(*) FROM public."' + name + '"')).scalar_one()
        v3._need(
            actual == sum(r["rows"] for r in sessions if r["dataset"] == name),
            "Application data table lacks complete restore-session coverage",
        )
    v3._need(
        all(r["dataset"] in universe and r["dataset"] not in _SCIENCE_TABLES for r in sessions),
        "Unexpected or conflicting universe session dataset",
    )
    snapshot = fence.check(db)
    if sessions:
        v3._need(snapshot["phase"] == "auditing", "Universe audit is not frozen")
    else:
        snapshot = fence.freeze(db)
    _application_context(db, contract, target, loading=False)
    return {
        "format": APPLICATION_FORMAT,
        "whole_sha256": whole_sha256,
        "target": target,
        "contract_sha256": binding,
        "tables": table_proofs,
        "parts": len(recorded),
        "rows": sum(totals.values()),
        "actual_application_tables": True,
        "full_native_digest_equal": True,
        "science_parent_fk_closure_proven": True,
        "universe_sessions_verified": True,
        "audit_epoch": snapshot["audit_epoch"],
        "source_accesses": 0,
        "full_restore_verified": True,
        "full_production_restore_accepted": False,
        "cross_database_atomic": False,
        "archive_accepted": False,
        "writer_allowed": False,
    }
