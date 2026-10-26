"""Independent catalog recovery, one verified part per bounded transaction.

Progress and recovered rows commit together. No partial database is accepted
as complete; the final proof requires every part, exact counts and integrity.
"""

import gzip
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from . import wav_catalog
from .policy import ArchiveBlocked, checksum

TABLES = {"records", "archive", "resume_info", "restored_parts", "table_counts"}


def full_integrity(db, destination, checkpoint):
    """Native full check; mapping changes I/O, never coverage or hard RAM limits."""
    db.execute("PRAGMA cache_size=-65536")
    db.execute("PRAGMA mmap_size=2147483648")
    target = str(destination)
    if hasattr(os, "posix_fadvise") and Path("/proc/self/fd").is_dir():
        for item in Path("/proc/self/fd").iterdir():
            try:
                if os.readlink(item) == target:
                    os.posix_fadvise(int(item.name), 0, 0, os.POSIX_FADV_SEQUENTIAL)
                    os.posix_fadvise(int(item.name), 0, 0, os.POSIX_FADV_NOREUSE)
            except (FileNotFoundError, OSError):
                pass  # Advice is optional; the complete check remains mandatory.
    interrupted = []

    def progress():
        try:
            checkpoint()
            return 0
        except Exception as error:
            interrupted.append(error)
            return 1

    db.set_progress_handler(progress, 20000)
    try:
        checkpoint()
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ArchiveBlocked("Recovered archive database is corrupt")
        checkpoint()
    except sqlite3.OperationalError:
        if interrupted:
            raise interrupted[0] from None
        raise
    finally:
        db.set_progress_handler(None, 0)


def private(path):
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_uid != os.getuid()
        or path.stat().st_mode & 0o077
    ):
        raise ArchiveBlocked("Private recovery file required")


def connect(manifest, destination):
    wav_catalog.manifest_check(manifest)
    if not destination.is_absolute() or destination.is_symlink():
        raise ArchiveBlocked("Absolute isolated archive database required")
    parent = destination.parent
    if (
        parent.is_symlink()
        or not parent.is_dir()
        or parent.stat().st_uid != os.getuid()
        or parent.stat().st_mode & 0o077
    ):
        raise ArchiveBlocked("Private isolated recovery directory required")
    digest = hashlib.sha256(wav_catalog.raw_json(manifest)).hexdigest()
    if destination.exists():
        private(destination)
        # Inspect before opening writable: never adopt a production/existing DB.
        with closing(sqlite3.connect(destination.as_uri() + "?mode=ro", uri=True)) as db:
            found = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if found != TABLES or db.execute("SELECT digest FROM resume_info").fetchall() != [
                (digest,)
            ]:
                raise ArchiveBlocked("Database is not this exact resumable recovery")
    else:
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(destination)) as db:
            db.execute(
                "CREATE TABLE records(kind TEXT,table_name TEXT,ordinal INTEGER,row TEXT,PRIMARY KEY(kind,table_name,ordinal))"
            )
            db.execute("CREATE TABLE archive(id TEXT PRIMARY KEY,state TEXT,receipt TEXT)")
            db.execute("CREATE TABLE resume_info(digest TEXT NOT NULL)")
            db.execute("INSERT INTO resume_info VALUES (?)", (digest,))
            db.execute(
                "CREATE TABLE restored_parts(position INTEGER PRIMARY KEY,file TEXT,sha256 TEXT,rows INTEGER,decoded INTEGER)"
            )
            db.execute(
                "CREATE TABLE table_counts(kind TEXT,table_name TEXT,n INTEGER,PRIMARY KEY(kind,table_name))"
            )
            db.executemany(
                "INSERT INTO table_counts VALUES (?,?,0)",
                [(kind, table) for kind, tables in wav_catalog.COLUMNS.items() for table in tables],
            )
            db.commit()
    db = sqlite3.connect(destination, timeout=2)
    db.execute("PRAGMA cache_size=-2048")
    db.execute("PRAGMA synchronous=FULL")
    return db


def restore_part(manifest, position, source, destination, checkpoint=lambda: None):
    if type(position) is not int or not 0 <= position < len(manifest.get("files", [])):
        raise ArchiveBlocked("Exact manifest part required")
    wav_catalog.manifest_check(manifest)
    entry = manifest["files"][position]
    private(source)
    checkpoint()
    if source.stat().st_size != entry["bytes"] or checksum(source) != entry["sha256"]:
        raise ArchiveBlocked("Independent part readback differs")
    with closing(connect(manifest, destination)) as db:
        prior = db.execute(
            "SELECT file,sha256,rows,decoded FROM restored_parts WHERE position=?", (position,)
        ).fetchone()
        expected = (entry["file"], entry["sha256"], entry["rows"], entry["decoded_bytes"])
        if prior:
            if prior != expected:
                raise ArchiveBlocked("Recovery part progress changed")
            return {"status": "reused", "position": position, "rows": entry["rows"]}
        if db.execute("SELECT position FROM restored_parts ORDER BY position").fetchall() != [
            (i,) for i in range(position)
        ]:
            raise ArchiveBlocked("Recovery parts must follow the exact manifest order")
        counts = {
            (kind, table): n
            for kind, table, n in db.execute("SELECT kind,table_name,n FROM table_counts")
        }
        rows = size = 0
        try:
            db.execute("BEGIN IMMEDIATE")
            with gzip.open(source, "rb") as body:
                while line := body.readline(wav_catalog.ROW_BYTES + 2):
                    size += len(line)
                    if len(line) > wav_catalog.ROW_BYTES + 1 or size > entry["decoded_bytes"]:
                        raise ArchiveBlocked("Part decoding exceeds its existing bounds")
                    value = json.loads(line)
                    kind = entry["kind"]
                    if kind == "ledger":
                        saved = wav_catalog.safe_receipt(value)
                        db.execute(
                            "INSERT INTO archive VALUES (?,?,?)",
                            (
                                saved["id"],
                                saved["state"],
                                wav_catalog.raw_json(saved["receipt"]).decode(),
                            ),
                        )
                    else:
                        wav_catalog.exact_keys(
                            value, {"table", "row"}, "metadata row", complete=True
                        )
                        table = value["table"]
                        wav_catalog.validate_row(kind, table, value["row"])
                        counts[kind, table] += 1
                        db.execute(
                            "INSERT INTO records VALUES (?,?,?,?)",
                            (
                                kind,
                                table,
                                counts[kind, table],
                                wav_catalog.raw_json(value["row"]).decode(),
                            ),
                        )
                    rows += 1
                    if rows % 100 == 0:
                        checkpoint()
            checkpoint()
            if (rows, size) != (entry["rows"], entry["decoded_bytes"]):
                raise ArchiveBlocked("Recovered part rows or bytes differ")
            db.executemany(
                "UPDATE table_counts SET n=? WHERE kind=? AND table_name=?",
                [(n, kind, table) for (kind, table), n in counts.items()],
            )
            db.execute("INSERT INTO restored_parts VALUES (?,?,?,?,?)", (position, *expected))
            db.commit()
        except BaseException:
            db.rollback()
            raise
    return {"status": "restored_part", "position": position, "rows": rows, "deleted_files": 0}


def finish(manifest, destination, reference, *, checkpoint=lambda: None):
    private(destination)
    checkpoint()
    with closing(connect(manifest, destination)) as db:
        actual = db.execute(
            "SELECT position,file,sha256,rows,decoded FROM restored_parts ORDER BY position"
        ).fetchall()
        expected = [
            (i, e["file"], e["sha256"], e["rows"], e["decoded_bytes"])
            for i, e in enumerate(manifest["files"])
        ]
        if actual != expected:
            raise ArchiveBlocked("Every complete catalog part must be recovered")
        counts = {kind: {} for kind in wav_catalog.COLUMNS}
        for kind, table, n in db.execute("SELECT kind,table_name,n FROM table_counts"):
            counts[kind][table] = n
            found = db.execute(
                "SELECT count(*) FROM records WHERE kind=? AND table_name=?", (kind, table)
            ).fetchone()[0]
            if found != n:
                raise ArchiveBlocked("Recovered rows differ from committed progress")
            checkpoint()
        if counts != manifest["tables"]:
            raise ArchiveBlocked("Full table coverage differs from the catalog")
        ledger_rows = sum(e["rows"] for e in manifest["files"] if e["kind"] == "ledger")
        if db.execute("SELECT count(*) FROM archive").fetchone()[0] != ledger_rows:
            raise ArchiveBlocked("Recovered journal count differs")
        full_integrity(db, destination, checkpoint)
        checkpoint()
    return {
        "schema": 2,
        "format": wav_catalog.FORMAT,
        "allowlist_sha256": wav_catalog.ALLOWLIST_SHA256,
        "environment": manifest["environment"],
        "destination": manifest["destination"],
        "created_at": datetime.now(UTC).isoformat(),
        "catalog_manifest": reference,
        "restored": [
            {k: e[k] for k in ("kind", "rows", "sha256", "file")} for e in manifest["files"]
        ],
        "tables": counts,
        "complete": True,
        "independent_read_identity": False,
        "deletion_readiness": False,
        "deleted_files": 0,
    }
