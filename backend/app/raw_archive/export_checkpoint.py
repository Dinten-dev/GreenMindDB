"""Verify and restore a fixed private export checkpoint into a new directory.

Never replaces live job stores, deletes spools, or grants worker ownership.
"""

import hashlib
import json
import os
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from .policy import ArchiveBlocked

MAX_BYTES = 32 * 1024**2


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as body:
        while chunk := body.read(1024**2):
            value.update(chunk)
    return value.hexdigest()


def verify(source, manifest_sha256):
    if source.is_symlink() or not source.is_dir():
        raise ArchiveBlocked("Private checkpoint directory required")
    manifest = source / "manifest.json"
    if (
        manifest.is_symlink()
        or not manifest.is_file()
        or manifest.stat().st_size > 65536
        or digest(manifest) != manifest_sha256
    ):
        raise ArchiveBlocked("Exact checkpoint manifest required")
    proof = json.loads(manifest.read_text())
    if (
        proof.get("schema") != 1
        or proof.get("complete") is not True
        or proof.get("originals_retained") is not True
        or proof.get("deleted_files") != 0
    ):
        raise ArchiveBlocked("Complete non-destructive checkpoint required")
    files = proof.get("files", {})
    if (
        not 1 <= len(files) <= 1000
        or "jobs.sqlite3" not in files
        or set(p.name for p in source.iterdir()) != set(files) | {"manifest.json"}
    ):
        raise ArchiveBlocked("Exact checkpoint file inventory required")
    if (
        any(type(v.get("bytes")) is not int or v["bytes"] < 0 for v in files.values())
        or sum(v["bytes"] for v in files.values()) > MAX_BYTES
    ):
        raise ArchiveBlocked("Checkpoint transfer budget exceeded")
    for name, value in files.items():
        path = source / name
        if (
            Path(name).name != name
            or path.is_symlink()
            or not path.is_file()
            or (name != "jobs.sqlite3" and not name.endswith((".zip", ".part", ".raw-part")))
        ):
            raise ArchiveBlocked("Unsafe checkpoint file")
        if path.stat().st_size != value["bytes"] or digest(path) != value["sha256"]:
            raise ArchiveBlocked("Checkpoint bytes differ")
    with closing(
        sqlite3.connect((source / "jobs.sqlite3").as_uri() + "?mode=ro", uri=True, timeout=2)
    ) as db:
        db.execute("PRAGMA query_only=ON")
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ArchiveBlocked("Checkpoint job database corrupt")
        rows = db.execute("SELECT * FROM jobs ORDER BY id").fetchall()
        if len(rows) != proof["jobs"]:
            raise ArchiveBlocked("Checkpoint job count differs")
        names = set(n for row in db.execute("SELECT parts FROM jobs") for n in json.loads(row[0]))
        if not names <= set(files) or any(
            Path(n).name != n or not n.endswith(".zip") for n in names
        ):
            raise ArchiveBlocked("Checkpoint referenced ZIP missing")
    return proof, rows


def restore(source, output, manifest_sha256):
    proof, rows = verify(source, manifest_sha256)
    if output.exists() or output.is_symlink():
        raise ArchiveBlocked("New empty destination required")
    output.mkdir(mode=0o700)
    # Source remains read-only; partial destination is retained after failure.
    with (
        closing(
            sqlite3.connect((source / "jobs.sqlite3").as_uri() + "?mode=ro", uri=True, timeout=2)
        ) as old,
        closing(sqlite3.connect(output / "jobs.sqlite3")) as new,
    ):
        old.backup(new)
        if (
            new.execute("PRAGMA integrity_check").fetchall() != [("ok",)]
            or new.execute("SELECT * FROM jobs ORDER BY id").fetchall() != rows
        ):
            raise ArchiveBlocked("Restored job ownership or contents differ")
    (output / "jobs.sqlite3").chmod(0o600)
    for name, value in proof["files"].items():
        if name == "jobs.sqlite3":
            continue
        with (source / name).open("rb") as old, (output / name).open("xb") as new:
            os.fchmod(new.fileno(), 0o600)
            shutil.copyfileobj(old, new, 1024**2)
            new.flush()
            os.fsync(new.fileno())
        if digest(output / name) != value["sha256"]:
            raise ArchiveBlocked("Restored spool differs")
    result = {
        "schema": 1,
        "source_manifest_sha256": manifest_sha256,
        "jobs": len(rows),
        "files": len(proof["files"]),
        "all_job_columns_preserved": True,
        "all_parts_verified": True,
        "live_store_changed": False,
        "ownership_accepted": False,
        "deleted_files": 0,
    }
    with (output / "restoration.json").open("x") as new:
        os.fchmod(new.fileno(), 0o600)
        json.dump(result, new)
    return result


def merge_into_new(source, existing, output, manifest_sha256):
    """Restore incoming jobs and retain every existing private job and spool.

    Caller must quiesce the private API/writer; neither source is ever changed.
    Conflicting job IDs fail closed. This never performs the live directory swap.
    """
    if existing.is_symlink() or not existing.is_dir():
        raise ArchiveBlocked("Existing private store required")
    result = restore(source, output, manifest_sha256)
    dbpath = existing / "jobs.sqlite3"
    if dbpath.is_symlink() or not dbpath.is_file():
        raise ArchiveBlocked("Existing job database required")
    with (
        closing(sqlite3.connect(dbpath.as_uri() + "?mode=ro", uri=True, timeout=2)) as old,
        closing(sqlite3.connect(output / "jobs.sqlite3")) as new,
    ):
        old.execute("PRAGMA query_only=ON")
        if old.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ArchiveBlocked("Existing private database corrupt")
        if (
            old.execute("PRAGMA table_info(jobs)").fetchall()
            != new.execute("PRAGMA table_info(jobs)").fetchall()
        ):
            raise ArchiveBlocked("Job schemas differ")
        original = old.execute("SELECT * FROM jobs ORDER BY id").fetchall()
        if len(original) > 1000:
            raise ArchiveBlocked("Private store merge exceeds bound")
        columns = [p[1] for p in old.execute("PRAGMA table_info(jobs)")]
        identity = columns.index("id")
        parts_column = columns.index("parts")
        for row in original:
            saved = new.execute("SELECT * FROM jobs WHERE id=?", (row[identity],)).fetchone()
            if saved is not None and saved != row:
                raise ArchiveBlocked("Conflicting job contents; both stores retained")
            if saved is None:
                new.execute("INSERT INTO jobs VALUES(" + ",".join("?" for _ in row) + ")", row)
        if new.execute("SELECT count(*) FROM jobs").fetchone()[0] > 1000:
            raise ArchiveBlocked("Combined private store exceeds job bound")
        new.commit()
        for row in original:
            if new.execute("SELECT * FROM jobs WHERE id=?", (row[identity],)).fetchone() != row:
                raise ArchiveBlocked("Existing ownership or progress changed")
        names = {n for row in original for n in json.loads(row[parts_column])}
        candidates = [
            p
            for p in existing.iterdir()
            if p.name != "jobs.sqlite3"
            and not p.name.startswith("jobs.sqlite3-")
            and p.name != "restoration.json"
        ]
        if (
            not names <= {p.name for p in candidates}
            or sum(p.stat().st_size for p in candidates) > MAX_BYTES
        ):
            raise ArchiveBlocked("Existing spool inventory exceeds bound")
        for oldfile in candidates:
            if (
                oldfile.is_symlink()
                or not oldfile.is_file()
                or not oldfile.name.endswith((".zip", ".part", ".raw-part"))
            ):
                raise ArchiveBlocked("Unsafe existing spool")
            target = output / oldfile.name
            if target.exists():
                if digest(target) != digest(oldfile):
                    raise ArchiveBlocked("Conflicting spool; originals retained")
            else:
                with oldfile.open("rb") as body, target.open("xb") as copy:
                    os.fchmod(copy.fileno(), 0o600)
                    shutil.copyfileobj(body, copy, 1024**2)
                    copy.flush()
                    os.fsync(copy.fileno())
                if digest(target) != digest(oldfile):
                    raise ArchiveBlocked("Existing spool copy differs")
        if sum(p.stat().st_size for p in output.iterdir() if p.is_file()) > MAX_BYTES:
            raise ArchiveBlocked("Combined private store exceeds byte bound")
        if new.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ArchiveBlocked("Combined private job database corrupt")
        result.update(
            existing_jobs_preserved=len(original),
            combined_jobs=new.execute("SELECT count(*) FROM jobs").fetchone()[0],
            live_store_changed=False,
        )
    with (output / "merge-restoration.json").open("x") as out:
        os.fchmod(out.fileno(), 0o600)
        json.dump(result, out)
    return result
