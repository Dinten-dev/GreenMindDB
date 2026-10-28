"""Durable oversized-revision backlog; queue entries never authorize deletion."""

from datetime import timedelta

from app.raw_archive.policy import ArchiveBlocked

from .codec import decode, digest, encode

MAX_PENDING = 5000
RETRY_DELAY = timedelta(days=1)


def summary(db):
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wav_pending'"
    ).fetchone():
        return {"pending_revisions": 0, "sources": {}}
    sources = {
        source: {"revisions": count, "oldest_seen": oldest, "next_retry": due}
        for source, count, oldest, due in db.execute(
            "SELECT source,COUNT(*),MIN(first_seen),MIN(due) FROM wav_pending GROUP BY source"
        )
    }
    return {
        "pending_revisions": sum(value["revisions"] for value in sources.values()),
        "sources": sources,
    }


class WavQueue:
    def __init__(self, db):
        self.db = db
        db.executescript("""
          CREATE TABLE IF NOT EXISTS wav_pending (
            id TEXT PRIMARY KEY, source TEXT NOT NULL, reference BLOB NOT NULL,
            first_seen TEXT NOT NULL, checked_at TEXT NOT NULL, due TEXT NOT NULL,
            attempts INTEGER NOT NULL, evidence BLOB NOT NULL);
          CREATE INDEX IF NOT EXISTS wav_pending_due ON wav_pending(source,due,id);
          CREATE TABLE IF NOT EXISTS wav_pending_event (
            ordinal INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
            id TEXT NOT NULL, state TEXT NOT NULL, evidence BLOB NOT NULL);
        """)

    def get(self, source, reference):
        return self.db.execute(
            "SELECT due FROM wav_pending WHERE id=?", (digest([source, reference]),)
        ).fetchone()

    def due(self, source, now):
        return [
            decode(row[0])
            for row in self.db.execute(
                "SELECT reference FROM wav_pending WHERE source=? AND due<=? "
                "ORDER BY due,id LIMIT 10",
                (source, now.isoformat()),
            )
        ]

    def defer(
        self,
        source,
        reference,
        records,
        now,
        *,
        max_wavs,
        max_bytes,
        reason="revision_exceeds_run_budget",
        retry_delay=RETRY_DELAY,
    ):
        # Caller commits this together with the discovery cursor. A crash must
        # never advance past an oversized revision without a durable explanation.
        identity = digest([source, reference])
        if not self.get(source, reference) and self.count() >= MAX_PENDING:
            raise ArchiveBlocked("WAV pending queue full; discovery cursor retained")
        evidence = encode(
            dict(
                source=source,
                reference=reference,
                reason=reason,
                wavs=len(records),
                bytes=sum(record.size for record in records),
                max_wavs=max_wavs,
                max_bytes=max_bytes,
                recordings_sha256=digest(
                    [(record.key, record.sha256, record.size) for record in records]
                ),
            )
        )
        self.db.execute(
            "INSERT INTO wav_pending VALUES(?,?,?,?,?,?,1,?) ON CONFLICT(id) DO UPDATE SET "
            "checked_at=excluded.checked_at,due=excluded.due,attempts=attempts+1,"
            "evidence=excluded.evidence",
            (
                identity,
                source,
                encode(reference),
                now.isoformat(),
                now.isoformat(),
                (now + retry_delay).isoformat(),
                evidence,
            ),
        )
        self.db.execute(
            "INSERT INTO wav_pending_event(at,id,state,evidence) VALUES(?,?,?,?)",
            (
                now.isoformat(),
                identity,
                "copying" if reason == "copy_in_progress" else "deferred",
                evidence,
            ),
        )

    def published(self, source, reference, receipt, now):
        identity = digest([source, reference])
        if not self.get(source, reference):
            return
        self.db.execute(
            "INSERT INTO wav_pending_event(at,id,state,evidence) VALUES(?,?,?,?)",
            (now.isoformat(), identity, "published", encode({"candidate": receipt["candidate"]})),
        )
        self.db.execute("DELETE FROM wav_pending WHERE id=?", (identity,))

    def count(self):
        return self.db.execute("SELECT COUNT(*) FROM wav_pending").fetchone()[0]
