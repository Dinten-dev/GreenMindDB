"""Bounded fixed-cohort reconciliation of an already independently restored DB.

The accepted recovery is always read-only. Only a separate private checkpoint
database changes; completed counts and cursor advance in the same transaction.
"""

import hashlib
import json
import re
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .catalog_resume import private
from .policy import ArchiveBlocked, checksum
from .wav_catalog import manifest_check, raw_json, save_json

PHASES = ("segments", "gateway", "direct")
FIELDS = (
    "files",
    "bytes",
    "matched",
    "pending",
    "pending_bytes",
    "receipt_mismatches",
    "invalid_metadata",
    "excluded",
    "eligible_pending_files",
    "seven_day_reserve_elapsed",
    "legacy_null_receipts",
    "nonnull_version_receipts",
)


def source_db(path):
    private(path)
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA cache_size=-2048")
    return db


def signature(path):
    st = path.stat()
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns]


def prepare(recovery, output, *, excluded=(), checkpoint=lambda: None):
    from .wav_catalog import private_directory

    checkpoint()
    manifest_path = recovery / "manifest.json"
    private(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    manifest_check(manifest)
    proof_path = recovery / "verified.json"
    private(proof_path)
    proof = json.loads(proof_path.read_text())
    if not proof.get("complete") or not proof.get("independent_read_identity"):
        raise ArchiveBlocked("Complete independent catalog recovery required")
    reference = proof.get("catalog_manifest", {})
    if (
        reference.get("sha256") != checksum(manifest_path)
        or reference.get("size") != manifest_path.stat().st_size
    ):
        raise ArchiveBlocked("Recovery reference differs from exact manifest")
    from .readiness import validate_metadata_restore

    validate_metadata_restore(manifest, proof)
    dbpath = recovery / "wav-catalog.sqlite3"
    private(dbpath)
    exclusion = sorted(set(tuple(x) for x in excluded))
    if any(k not in ("gateway", "direct") or not isinstance(i, str) for k, i in exclusion):
        raise ArchiveBlocked("Exact fixed quarantine identities required")
    private_directory(output)
    with closing(sqlite3.connect(output / "progress.sqlite3")) as db:
        db.execute("CREATE TABLE meta(value TEXT NOT NULL)")
        db.execute(
            "CREATE TABLE progress(phase TEXT PRIMARY KEY,cursor INTEGER NOT NULL,done INTEGER NOT NULL)"
        )
        db.execute("CREATE TABLE counts(kind TEXT PRIMARY KEY,value TEXT NOT NULL)")
        db.execute(
            "CREATE TABLE segments(id TEXT PRIMARY KEY,published INTEGER,sealed INTEGER,updated REAL)"
        )
        db.execute(
            "CREATE TABLE quarantine(kind TEXT,identity TEXT,reason TEXT,PRIMARY KEY(kind,identity))"
        )
        db.execute(
            "INSERT INTO meta VALUES (?)",
            (
                raw_json(
                    {
                        "recovery": str(recovery),
                        "manifest_sha256": checksum(manifest_path),
                        "proof_sha256": checksum(proof_path),
                        "source_signature": signature(dbpath),
                        "excluded": exclusion,
                    }
                ).decode(),
            ),
        )
        db.executemany("INSERT INTO progress VALUES (?,0,0)", [(p,) for p in PHASES])
        db.executemany(
            "INSERT INTO counts VALUES (?,?)",
            [(k, raw_json(dict.fromkeys(FIELDS, 0)).decode()) for k in ("gateway", "direct")],
        )
        db.commit()
    (output / "progress.sqlite3").chmod(0o600)
    return {"status": "prepared", "deleted_files": 0}


def phase(output, name, *, limit=10000, checkpoint=lambda: None):
    if name not in PHASES or type(limit) is not int or not 1 <= limit <= 10000:
        raise ArchiveBlocked("Bounded reconciliation phase required")
    path = output / "progress.sqlite3"
    private(path)
    began = time.monotonic()

    def guard():
        checkpoint()
        if time.monotonic() - began >= 80:
            raise ArchiveBlocked("Reconciliation phase deadline")

    guard()
    with closing(sqlite3.connect(path, timeout=2)) as state:
        state.execute("PRAGMA cache_size=-2048")
        state.execute("PRAGMA synchronous=FULL")
        meta = json.loads(state.execute("SELECT value FROM meta").fetchone()[0])
        recovery = Path(meta["recovery"])
        dbpath = recovery / "wav-catalog.sqlite3"
        if (
            signature(dbpath) != meta["source_signature"]
            or checksum(recovery / "manifest.json") != meta["manifest_sha256"]
            or checksum(recovery / "verified.json") != meta["proof_sha256"]
        ):
            raise ArchiveBlocked("Accepted fixed recovery changed")
        manifest = json.loads((recovery / "manifest.json").read_text())
        cutoff = datetime.fromisoformat(manifest["created_at"])
        if cutoff.tzinfo is None:
            raise ArchiveBlocked("Fixed UTC cutoff required")
        cursor, done = state.execute(
            "SELECT cursor,done FROM progress WHERE phase=?", (name,)
        ).fetchone()
        if done:
            return {"status": "reused", "phase": name, "deleted_files": 0}
        if any(
            not row[0]
            for row in state.execute(
                "SELECT done FROM progress WHERE phase IN ("
                + ",".join("?" for _ in PHASES[: PHASES.index(name)])
                + ")",
                PHASES[: PHASES.index(name)],
            )
        ):
            raise ArchiveBlocked("Prior reconciliation phases incomplete")
        kind, table = (
            ("direct", "direct_segment")
            if name == "segments"
            else ("gateway", "wav_file")
            if name == "gateway"
            else ("direct", "direct_revision")
        )
        counts = json.loads(
            state.execute("SELECT value FROM counts WHERE kind=?", (kind,)).fetchone()[0]
        )
        excluded = {tuple(x) for x in meta["excluded"]}
        with closing(source_db(dbpath)) as source:

            def accept(identity, bucket, key, digest, size, ended, received):
                counts["files"] += 1
                counts["bytes"] += size if type(size) is int and size > 0 else 0
                if (kind, identity) in excluded:
                    counts["excluded"] += 1
                    state.execute(
                        "INSERT OR IGNORE INTO quarantine VALUES (?,?,?)",
                        (kind, identity, "operator_excluded"),
                    )
                    return
                if (
                    not isinstance(digest, str)
                    or not re.fullmatch("[a-f0-9]{64}", digest)
                    or type(size) is not int
                    or size <= 0
                ):
                    counts["invalid_metadata"] += 1
                    state.execute(
                        "INSERT OR IGNORE INTO quarantine VALUES (?,?,?)",
                        (kind, identity, "invalid_metadata"),
                    )
                    return
                ident = hashlib.sha256(raw_json([kind, bucket, key])).hexdigest()
                saved = source.execute(
                    "SELECT state,receipt FROM archive WHERE id=?", (ident,)
                ).fetchone()
                receipt = json.loads(saved[1]) if saved and saved[0] == "verified" else None
                record = receipt.get("recording", {}) if receipt else {}
                matched = tuple(
                    record.get(f) for f in ("kind", "bucket", "key", "sha256", "size")
                ) == (kind, bucket, key, digest, size)
                counts["matched" if matched else "pending"] += 1
                if not matched:
                    counts["pending_bytes"] += size
                if receipt and not matched:
                    counts["receipt_mismatches"] += 1
                stamps = [ended, received]
                if receipt:
                    stamps.append(
                        datetime.fromisoformat(
                            receipt.get("first_verified_at", receipt["verified_at"])
                        )
                    )
                    version = receipt.get("snapshot", {}).get("VersionId")
                    counts[
                        "legacy_null_receipts"
                        if version in (None, "", "null")
                        else "nonnull_version_receipts"
                    ] += 1
                if max(stamps) + timedelta(days=7) <= cutoff:
                    counts["seven_day_reserve_elapsed"] += int(matched)
                    counts["eligible_pending_files"] += int(not matched)

            state.execute("BEGIN IMMEDIATE")
            rows = source.execute(
                "SELECT ordinal,row FROM records WHERE kind=? AND table_name=? AND ordinal>? ORDER BY ordinal LIMIT ?",
                (kind, table, cursor, limit),
            )
            processed = 0
            for ordinal, body in rows:
                if processed % 100 == 0:
                    guard()
                row = json.loads(body)
                processed += 1
                cursor = ordinal
                if name == "segments":
                    state.execute(
                        "INSERT INTO segments VALUES (?,?,?,?)",
                        (row["id"], row["published_revision"], row["sealed"], row["updated_at"]),
                    )
                elif name == "gateway" and row["raw_deleted_at"] is None:
                    accept(
                        row["id"],
                        "greenmind-raw",
                        row["s3_key"],
                        row["content_sha256"],
                        row["file_size_bytes"],
                        datetime.fromisoformat(row["ended_at"]),
                        datetime.fromisoformat(row["created_at"]),
                    )
                elif name == "direct":
                    segment = state.execute(
                        "SELECT published,sealed,updated FROM segments WHERE id=?",
                        (row["segment_id"],),
                    ).fetchone()
                    if (
                        not segment
                        or segment[:2] != (row["revision"], 1)
                        or row["raw_deleted_at"] is not None
                    ):
                        continue
                    cfg = row["manifest"]["config"]
                    bucket = manifest["direct_bucket"]
                    for run in row["manifest"]["runs"]:
                        ended = max(
                            run["started_at_us"] / 1e6 + run["frame_count"] / cfg["sample_rate"],
                            row["manifest"]["bucket_start_us"] / 1e6 + 600,
                        )
                        accept(
                            row["segment_id"] + ":" + str(row["revision"]),
                            bucket,
                            run["key"],
                            run["sha256"],
                            44 + run["frame_count"] * cfg["channels"] * cfg["sample_bits"] // 8,
                            datetime.fromtimestamp(ended, UTC),
                            datetime.fromtimestamp(max(segment[2], row["verified_at"]), UTC),
                        )
            done = int(cursor == manifest["tables"][kind][table])
            if processed == 0 and not done:
                raise ArchiveBlocked("Fixed inventory has a missing ordinal")
            guard()
            state.execute("UPDATE progress SET cursor=?,done=? WHERE phase=?", (cursor, done, name))
            state.execute(
                "UPDATE counts SET value=? WHERE kind=?", (raw_json(counts).decode(), kind)
            )
            state.commit()
        return {
            "status": "phase_complete" if done else "phase_progress",
            "phase": name,
            "cursor": cursor,
            "deleted_files": 0,
        }


def finish(output, checkpoint=lambda: None):
    checkpoint()
    path = output / "progress.sqlite3"
    private(path)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        if db.execute("SELECT count(*) FROM progress WHERE done<>1").fetchone()[0]:
            raise ArchiveBlocked("Every fixed-cohort phase required")
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ArchiveBlocked("Reconciliation progress corrupt")
        meta = json.loads(db.execute("SELECT value FROM meta").fetchone()[0])
        counts = {k: json.loads(v) for k, v in db.execute("SELECT kind,value FROM counts")}
        quarantine = [
            {"kind": k, "identity": i, "reason": r}
            for k, i, r in db.execute(
                "SELECT kind,identity,reason FROM quarantine ORDER BY kind,identity"
            )
        ]
    recovery = Path(meta["recovery"])
    manifest = json.loads((recovery / "manifest.json").read_text())
    if (
        signature(recovery / "wav-catalog.sqlite3") != meta["source_signature"]
        or checksum(recovery / "manifest.json") != meta["manifest_sha256"]
        or checksum(recovery / "verified.json") != meta["proof_sha256"]
    ):
        raise ArchiveBlocked("Fixed catalog changed")
    result = {
        "schema": 2,
        "environment": manifest["environment"],
        "cutoff": manifest["created_at"],
        "inventory_sha256": meta["manifest_sha256"],
        "complete": True,
        "counts": counts,
        "eligible_pending_files": sum(c["eligible_pending_files"] for c in counts.values()),
        "receipt_mismatches": sum(c["receipt_mismatches"] for c in counts.values()),
        "unknown_eligible_files": sum(c["invalid_metadata"] for c in counts.values()),
        "remote_physical_readback": False,
        "legacy_null_accepted": False,
        "deletion_ready": False,
        "deleted_files": 0,
    }
    save_json(
        output / "quarantine.json",
        {"schema": 1, "environment": manifest["environment"], "excluded": quarantine},
    )
    save_json(output / "reconciliation.json", result)
    return result
