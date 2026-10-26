"""Complete SQLite structure and primary-index checks in bounded read-only phases.

quick_check covers the entire physical file. Its two omitted checks (UNIQUE
and index/table agreement) are supplied by an exact rowid/key bijection for
every index in this fixed recovery schema. No sampling or quick-only acceptance.
The recovered database never changes; only a separate private progress DB does.
"""

import hashlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from .catalog_resume import TABLES, private
from .policy import ArchiveBlocked, checksum
from .wav_catalog import manifest_check, private_directory, raw_json, save_json

INDEXES = {
    "records": ("sqlite_autoindex_records_1", ("kind", "table_name", "ordinal")),
    "archive": ("sqlite_autoindex_archive_1", ("id",)),
    "table_counts": ("sqlite_autoindex_table_counts_1", ("kind", "table_name")),
}
METHOD = "full-quick-check-and-all-primary-index-bijections-v1"


def expected_counts(manifest):
    manifest_check(manifest)
    return {
        "records": sum(sum(v.values()) for v in manifest["tables"].values()),
        "archive": sum(e["rows"] for e in manifest["files"] if e["kind"] == "ledger"),
        "table_counts": sum(len(v) for v in manifest["tables"].values()),
    }


def signature(path):
    s = path.stat()
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns]


def connect(path):
    private(path)
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA cache_size=-2048")
    return db


def schema(db):
    tables = {x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if tables != TABLES:
        raise ArchiveBlocked("Exact recovery tables required")
    if db.execute("SELECT 1 FROM sqlite_master WHERE type NOT IN ('table','index')").fetchone():
        raise ArchiveBlocked("Unexpected recovery schema object")
    for table in sorted(TABLES):
        found = db.execute("PRAGMA index_list(" + table + ")").fetchall()
        expected = INDEXES.get(table)
        if expected:
            if len(found) != 1 or found[0][1:5] != (expected[0], 1, "pk", 0):
                raise ArchiveBlocked("Exact unique recovery index required")
            if (
                tuple(r[2] for r in db.execute("PRAGMA index_info(" + expected[0] + ")"))
                != expected[1]
            ):
                raise ArchiveBlocked("Recovery key columns changed")
        elif found:
            raise ArchiveBlocked("Unexpected recovery index")
    rows = db.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
    ).fetchall()
    return hashlib.sha256(raw_json(rows)).hexdigest()


def prepare(recovery, output, checkpoint=lambda: None):
    checkpoint()
    database = recovery / "wav-catalog.sqlite3"
    manifest = recovery / "manifest.json"
    private(database)
    private(manifest)
    if manifest.stat().st_size > 1024**2:
        raise ArchiveBlocked("Bounded manifest required")
    required = expected_counts(json.loads(manifest.read_text()))
    with closing(connect(database)) as db:
        digest = schema(db)
        expected = {
            name: db.execute(
                "SELECT count(rowid) FROM " + name + " INDEXED BY " + index
            ).fetchone()[0]
            for name, (index, _) in INDEXES.items()
        }
        checkpoint()
        if expected != required:
            raise ArchiveBlocked("Index coverage differs from complete manifest")
    private_directory(output)
    with closing(sqlite3.connect(output / "progress.sqlite3")) as state:
        state.execute("CREATE TABLE meta(value TEXT NOT NULL)")
        state.execute(
            "CREATE TABLE progress(name TEXT PRIMARY KEY,cursor INTEGER,checked INTEGER,done INTEGER)"
        )
        state.execute(
            "INSERT INTO meta VALUES (?)",
            (
                raw_json(
                    {
                        "database": str(database),
                        "signature": signature(database),
                        "manifest_sha256": checksum(manifest),
                        "schema_sha256": digest,
                        "expected": expected,
                    }
                ).decode(),
            ),
        )
        state.executemany(
            "INSERT INTO progress VALUES (?,0,0,0)", [(x,) for x in ("quick", *INDEXES)]
        )
        state.commit()
    (output / "progress.sqlite3").chmod(0o600)
    return {"status": "prepared", "deletion_ready": False, "deleted_files": 0}


def phase(output, name, checkpoint=lambda: None, *, limit=10000):
    if name not in ("quick", *INDEXES) or type(limit) is not int or not 1 <= limit <= 10000:
        raise ArchiveBlocked("Bounded integrity phase required")
    private(output / "progress.sqlite3")
    began = time.monotonic()

    def guard():
        checkpoint()
        if time.monotonic() - began >= 90:
            raise ArchiveBlocked("Integrity phase deadline")

    guard()
    with closing(sqlite3.connect(output / "progress.sqlite3", timeout=2)) as state:
        state.execute("PRAGMA synchronous=FULL")
        state.execute("PRAGMA cache_size=-2048")
        meta = json.loads(state.execute("SELECT value FROM meta").fetchone()[0])
        database = Path(meta["database"])
        if (
            signature(database) != meta["signature"]
            or checksum(database.parent / "manifest.json") != meta["manifest_sha256"]
        ):
            raise ArchiveBlocked("Fixed recovery changed")
        cursor, checked, done = state.execute(
            "SELECT cursor,checked,done FROM progress WHERE name=?", (name,)
        ).fetchone()
        if done:
            return {"status": "reused", "phase": name, "deleted_files": 0}
        with closing(connect(database)) as db:
            if schema(db) != meta["schema_sha256"]:
                raise ArchiveBlocked("Fixed schema changed")
            state.execute("BEGIN IMMEDIATE")
            if name == "quick":
                if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise ArchiveBlocked("Full database structure corrupt")
                guard()
                done = 1
            else:
                if not state.execute("SELECT done FROM progress WHERE name='quick'").fetchone()[0]:
                    raise ArchiveBlocked("Complete structure check required first")
                index, columns = INDEXES[name]
                rows = db.execute(
                    "SELECT rowid,"
                    + ",".join(columns)
                    + " FROM "
                    + name
                    + " NOT INDEXED WHERE rowid>? ORDER BY rowid LIMIT ?",
                    (cursor, limit),
                ).fetchall()
                lookup = (
                    "SELECT rowid,"
                    + ",".join(columns)
                    + " FROM "
                    + name
                    + " INDEXED BY "
                    + index
                    + " WHERE "
                    + " AND ".join(c + " IS ?" for c in columns)
                    + " LIMIT 2"
                )
                for i, row in enumerate(rows):
                    if i % 1000 == 0:
                        guard()
                    if db.execute(lookup, row[1:]).fetchall() != [row]:
                        raise ArchiveBlocked("Unique index and table are not an exact bijection")
                    cursor = row[0]
                    checked += 1
                # Prove every table row and every index entry was covered, not a sample.
                remaining = db.execute(
                    "SELECT 1 FROM " + name + " NOT INDEXED WHERE rowid>? LIMIT 1", (cursor,)
                ).fetchone()
                if remaining is None:
                    if checked != meta["expected"][name]:
                        raise ArchiveBlocked("Missing or surplus index entries")
                    done = 1
                guard()
            if signature(database) != meta["signature"]:
                raise ArchiveBlocked("Recovery changed during integrity phase")
            state.execute(
                "UPDATE progress SET cursor=?,checked=?,done=? WHERE name=?",
                (cursor, checked, done, name),
            )
            state.commit()
        return {
            "status": "phase_complete" if done else "phase_progress",
            "phase": name,
            "checked": checked,
            "deleted_files": 0,
        }


def finish(output, checkpoint=lambda: None):
    checkpoint()
    private(output / "progress.sqlite3")
    with closing(connect(output / "progress.sqlite3")) as state:
        if state.execute("SELECT count(*) FROM progress WHERE done<>1").fetchone()[0]:
            raise ArchiveBlocked("All complete integrity phases required")
        if state.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ArchiveBlocked("Integrity progress corrupt")
        meta = json.loads(state.execute("SELECT value FROM meta").fetchone()[0])
        checked = {
            n: v for n, v in state.execute("SELECT name,checked FROM progress WHERE name<>'quick'")
        }
    if checked != meta["expected"] or signature(Path(meta["database"])) != meta["signature"]:
        raise ArchiveBlocked("Complete fixed integrity coverage required")
    result = {
        **meta,
        "method": METHOD,
        "complete": True,
        "all_index_rows_verified": checked,
        "quick_check_alone_accepted": False,
        "deleted_files": 0,
        "deletion_ready": False,
    }
    save_json(output / "verified.json", result)
    return result


def validate(proof_path, database, manifest):
    private(proof_path)
    if proof_path.stat().st_size > 8192:
        raise ArchiveBlocked("Bounded integrity proof required")
    proof = json.loads(proof_path.read_text())
    if (
        proof.get("method") != METHOD
        or proof.get("complete") is not True
        or proof.get("quick_check_alone_accepted") is not False
        or proof.get("database") != str(database)
        or proof.get("signature") != signature(database)
        or proof.get("manifest_sha256") != checksum(manifest)
        or proof.get("all_index_rows_verified") != proof.get("expected")
        or proof.get("expected") != expected_counts(json.loads(manifest.read_text()))
    ):
        raise ArchiveBlocked("Exact complete integrity proof required")
    with closing(connect(database)) as db:
        if schema(db) != proof.get("schema_sha256"):
            raise ArchiveBlocked("Accepted integrity schema changed")
    return proof
