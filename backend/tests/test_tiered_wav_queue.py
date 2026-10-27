"""Scheduler integration: durable deferral, budgets, retries and crash boundaries."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.raw_archive.health import SafetyPause
from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import schedule, wav_queue
from app.tiered_archive.codec import decode
from app.tiered_archive.ledger import Ledger

AT = datetime(2026, 10, 27, tzinfo=UTC)
MIB = 1024**2


@pytest.fixture
def harness(tmp_path, monkeypatch):
    from app.tiered_archive import runtime

    root = tmp_path / "state"
    rows = {}
    uploads = []
    inspected = []
    now = [AT]

    class Catalog:
        def page(self, cursor, before, limit):
            return [ref for ref in sorted(rows) if cursor is None or ref > tuple(cursor)][:limit]

        def resolve(self, reference):
            inspected.append(tuple(reference))
            value = rows[tuple(reference)]
            if isinstance(value, Exception):
                raise value
            return [
                SimpleNamespace(size=size, key=f"{reference[0]}/{i}", sha256="a" * 64)
                for i, size in enumerate(value)
            ]

    def publish(source, reference, maximum_bytes, maximum_wavs):
        sizes = rows[tuple(reference)]
        assert sum(sizes) <= maximum_bytes
        assert len(sizes) <= maximum_wavs
        uploads.append(tuple(reference))
        return {
            "candidate": "receipt-" + str(reference),
            "files": [{"format": "wav", "bytes": size} for size in sizes],
        }

    @contextmanager
    def worker():
        ledger = Ledger(root)
        try:
            yield ledger, None, lambda: None
        finally:
            ledger.close()

    monkeypatch.setattr(schedule, "environment", lambda: "staging")
    monkeypatch.setattr(schedule, "state_root", lambda: root)

    class Healthy:
        def __call__(self):
            return True

        def close(self):
            pass

    monkeypatch.setattr(schedule, "health_probe", lambda kind: Healthy())
    monkeypatch.setattr(runtime, "wav_source", lambda *args: SimpleNamespace(catalog=Catalog()))
    monkeypatch.setattr(runtime, "publish_wav", publish)
    monkeypatch.setattr(runtime, "worker", worker)
    monkeypatch.setenv("TIERED_ARCHIVE_DELETE_ENABLED", "false")
    path = tmp_path / "schedule.json"
    path.write_text(json.dumps(dict(schema=1, environment="staging", wav_sources=["direct"])))
    path.chmod(0o600)
    return SimpleNamespace(
        root=root,
        rows=rows,
        uploads=uploads,
        inspected=inspected,
        now=now,
        tick=lambda: schedule.tick(path, clock=lambda: now[0]),
    )


def query(h, sql):
    with sqlite3.connect(h.root / "tiered.sqlite3") as db:
        return db.execute(sql).fetchall()


def test_187_run_revision_is_durable_and_does_not_starve_next_wav(harness):
    h = harness
    h.rows.update({("a", 187): [804] * 187, ("b", 601): [456044]})
    result = h.tick()
    assert result["published"] == 1
    assert result["deferred"] == result["blocked"] == result["pending_wavs"] == 1
    assert result["evicted"] == 0
    assert h.uploads == [("b", 601)]
    raw, attempts, first = query(h, "SELECT evidence,attempts,first_seen FROM wav_pending")[0]
    proof = decode(raw)
    assert proof["reference"] == ["a", 187]
    assert proof["wavs"] == 187 and proof["bytes"] == 150348
    assert proof["max_wavs"] == 10 and proof["max_bytes"] == 5 * MIB
    assert len(proof["recordings_sha256"]) == 64
    assert attempts == 1 and first == AT.isoformat()
    assert decode(query(h, "SELECT cursor FROM sweep")[0][0]) == ["b", 601]
    h.now[0] += timedelta(minutes=5)
    assert h.tick()["pending_wavs"] == 1
    assert h.inspected.count(("a", 187)) == 1
    h.now[0] += timedelta(days=1)
    h.tick()
    assert h.inspected.count(("a", 187)) == 2
    assert query(h, "SELECT attempts,first_seen FROM wav_pending") == [(2, first)]


@pytest.mark.parametrize("sizes", [[5 * MIB + 1], [1] * 11])
def test_oversized_revision_never_uploads_any_part(harness, sizes):
    h = harness
    h.rows.update({("a", 1): sizes, ("b", 1): [1]})
    assert h.tick()["pending_wavs"] == 1
    assert h.uploads == [("b", 1)]


@pytest.mark.parametrize(
    "first,second", [([1] * 10, [1]), ([5 * MIB], [1]), ([3 * MIB], [3 * MIB])]
)
def test_combined_run_budget_keeps_next_cursor_without_permanent_deferral(harness, first, second):
    h = harness
    h.rows.update({("a", 1): first, ("b", 1): second})
    assert h.tick()["pending_wavs"] == 0
    assert h.uploads == [("a", 1)]
    assert decode(query(h, "SELECT cursor FROM sweep")[0][0]) == ["a", 1]
    assert h.tick()["published"] == 1
    assert h.uploads == [("a", 1), ("b", 1)]


def test_crash_between_deferral_and_cursor_rolls_back_both(harness, monkeypatch):
    h = harness
    h.rows[("a", 1)] = [1] * 11
    original = schedule.encode
    calls = []

    def crash(value):
        calls.append(value)
        if len(calls) == 2:  # process token first, discovery cursor second
            raise RuntimeError("synthetic crash before cursor commit")
        return original(value)

    monkeypatch.setattr(schedule, "encode", crash)
    with pytest.raises(RuntimeError, match="synthetic crash"):
        h.tick()
    assert not query(h, "SELECT * FROM wav_pending")
    assert not query(h, "SELECT * FROM wav_pending_event")
    assert not query(h, "SELECT * FROM sweep")
    monkeypatch.setattr(schedule, "encode", original)
    assert h.tick()["pending_wavs"] == 1


def test_full_backlog_preserves_unqueued_cursor_and_revisions(harness, monkeypatch):
    h = harness
    monkeypatch.setattr(wav_queue, "MAX_PENDING", 1)
    h.rows.update({("a", 1): [1] * 11, ("b", 2): [1] * 12})
    with pytest.raises(ArchiveBlocked, match="queue full"):
        h.tick()
    assert len(query(h, "SELECT * FROM wav_pending")) == 1
    assert decode(query(h, "SELECT cursor FROM sweep")[0][0]) == ["a", 1]
    assert not h.uploads


def test_retry_revalidates_and_records_resolution_without_duplicate_upload(harness):
    h = harness
    h.rows[("a", 1)] = [1] * 11
    h.tick()
    h.tick()  # exhaustion resets discovery cursor, with tomorrow's due time
    h.now[0] += timedelta(days=1)
    h.rows[("a", 1)] = [1]
    result = h.tick()
    assert result["published"] == 1 and result["pending_wavs"] == 0
    assert h.uploads == [("a", 1)]
    assert query(h, "SELECT state FROM wav_pending_event ORDER BY ordinal") == [
        ("deferred",),
        ("published",),
    ]


@pytest.mark.parametrize("error", [ArchiveBlocked("source changed"), SafetyPause("load")])
def test_failed_recheck_keeps_queue_and_cursor(harness, error):
    h = harness
    h.rows[("a", 1)] = [1] * 11
    h.tick()
    before = query(h, "SELECT * FROM wav_pending")
    cursor = query(h, "SELECT * FROM sweep")
    h.rows[("a", 1)] = error
    h.now[0] += timedelta(days=1)
    with pytest.raises(type(error)):
        h.tick()
    assert query(h, "SELECT * FROM wav_pending") == before
    assert query(h, "SELECT * FROM sweep") == cursor
    assert not h.uploads


def test_resolved_count_is_checked_again_before_source_download(tmp_path):
    from app.tiered_archive.wav_source import WavSource

    # Simulates a different count from the scheduler's earlier planning read.
    catalog = SimpleNamespace(resolve=lambda _: [SimpleNamespace(size=1)] * 11)
    adapter = WavSource(None, catalog, None, "direct", "staging", None, lambda: None)
    with pytest.raises(ArchiveBlocked, match="bounded file count"):
        adapter.export(["revision", 1], tmp_path, maximum_bytes=5 * MIB, maximum_wavs=10)
    assert not list(tmp_path.iterdir())


def test_queue_status_reports_backlog_without_source_identifiers(harness):
    h = harness
    ledger = Ledger(h.root)
    try:
        assert wav_queue.summary(ledger.db) == {"pending_revisions": 0, "sources": {}}
    finally:
        ledger.close()
    h.rows[("private-source-id", 1)] = [1] * 11
    h.tick()
    with sqlite3.connect(h.root / "tiered.sqlite3") as db:
        result = wav_queue.summary(db)
    assert result["pending_revisions"] == 1
    assert result["sources"]["direct"]["oldest_seen"] == AT.isoformat()
    assert "private-source-id" not in json.dumps(result)
