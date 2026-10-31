"""Durable per-revision proofs, intents and cache-independent archive index."""

import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.raw_archive.policy import ArchiveBlocked

from .policy import POLICY_SHA256


class Ledger:
    def __init__(self, root, *, read_only=False):
        root = Path(root)
        if not root.is_absolute() or root.is_symlink():
            raise ArchiveBlocked("Isolated absolute archive state required")
        self.read_only = read_only
        if read_only:
            self.root = root
            self.db = sqlite3.connect(
                (root / "catalog-read.sqlite3").as_uri() + "?mode=ro&immutable=1",
                uri=True,
                timeout=3,
            )
            self.db.execute("PRAGMA query_only=ON")
            return
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root = root
        self.db = sqlite3.connect(root / "tiered.sqlite3", timeout=3)
        os.chmod(root / "tiered.sqlite3", 0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS bundle (
            id TEXT PRIMARY KEY, environment TEXT NOT NULL, source TEXT NOT NULL,
            organization TEXT NOT NULL, dataset TEXT NOT NULL, start TEXT NOT NULL,
            end TEXT NOT NULL, receipt TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS bundle_scope ON bundle(environment,source,organization,dataset,start,end);
          CREATE TABLE IF NOT EXISTS event (
            ordinal INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
            candidate TEXT NOT NULL, state TEXT NOT NULL, body TEXT NOT NULL);
        """)

    def close(self):
        self.db.close()

    def get(self, identity):
        row = self.db.execute("SELECT receipt FROM bundle WHERE id=?", (identity,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, candidate, state, data, now=None):
        if self.read_only:
            raise ArchiveBlocked("Read API cannot modify archive proofs")
        now = now or datetime.now(UTC)
        old = self.get(candidate.identity)
        allowed = {
            None: {"published"},
            "published": {"published", "verified"},
            "verified": {"verified", "deleting"},
            "deleting": {"deleting", "evicted"},
            "evicted": {"evicted"},
        }
        if state not in allowed[old["state"] if old else None]:
            raise ArchiveBlocked("Invalid archive state transition")
        data = dict(data, state=state, candidate=candidate.identity, policy=POLICY_SHA256)
        if old and old.get("first_verified_at"):
            data["first_verified_at"] = old["first_verified_at"]
        elif state == "verified":
            data["first_verified_at"] = now.isoformat()
        raw = json.dumps(data, sort_keys=True, allow_nan=False)
        with self.db:
            self.db.execute(
                "INSERT INTO bundle VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET receipt=excluded.receipt",
                (
                    candidate.identity,
                    candidate.environment,
                    candidate.source,
                    candidate.organization,
                    candidate.dataset,
                    candidate.start.isoformat(),
                    candidate.end.isoformat(),
                    raw,
                ),
            )
            self.db.execute(
                "INSERT INTO event(at,candidate,state,body) VALUES(?,?,?,?)",
                (now.isoformat(), candidate.identity, state, raw),
            )
        self.publish_read_view()
        return data

    def audit_attempt(self, candidate, state, details, now):
        """Retain failed checks without changing the last valid archive receipt."""
        if self.read_only:
            raise ArchiveBlocked("Read API cannot modify archive proofs")
        if state != "wav_delete_blocked":
            raise ArchiveBlocked("Unsupported archive audit event")
        body = json.dumps(details, sort_keys=True, allow_nan=False)
        with self.db:
            self.db.execute(
                "INSERT INTO event(at,candidate,state,body) VALUES(?,?,?,?)",
                (now.isoformat(), candidate.identity, state, body),
            )

    def publish_read_view(self):
        path = self.root / ("read-view-" + uuid.uuid4().hex + ".sqlite3")
        try:
            with sqlite3.connect(path) as target:
                self.db.backup(target)
                target.execute("PRAGMA journal_mode=DELETE")
            os.chmod(path, 0o600)
            group = os.environ.get("TIERED_ARCHIVE_READ_GID")
            if group:
                os.chown(path, -1, int(group))
                os.chmod(path, 0o640)
            with path.open("rb") as body:
                os.fsync(body.fileno())
            os.replace(path, self.root / "catalog-read.sqlite3")
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            path.unlink(missing_ok=True)

    def scope(self, environment, source, organization, dataset, start, end):
        rows = self.db.execute(
            """SELECT receipt FROM bundle WHERE environment=? AND source=?
           AND organization=? AND dataset=? AND start<? AND end>? ORDER BY start,id""",
            (environment, source, organization, dataset, end.isoformat(), start.isoformat()),
        )
        return [json.loads(row[0]) for row in rows]

    def status(self):
        result = {}
        for (raw,) in self.db.execute("SELECT receipt FROM bundle"):
            state = json.loads(raw)["state"]
            result[state] = result.get(state, 0) + 1
        return result
