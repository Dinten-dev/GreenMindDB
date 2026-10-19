"""Long native integrity checks retain live guards and cannot emit partial PASS."""

import sqlite3

import pytest

from app.raw_archive.catalog_finish_guard import CatalogFinishGuard
from app.raw_archive.catalog_resume import full_integrity
from app.raw_archive.health import SafetyPause


class Probe:
    def __init__(self, ok=True):
        self.ok, self.closed = ok, False
        self.last = {"reason": None if ok else "source_memory"}

    def __call__(self):
        return self.ok

    def close(self):
        self.closed = True


def test_initial_pause_never_starts_monitor():
    probe = Probe(False)
    guard = CatalogFinishGuard(probe, deadline=110, clock=lambda: 0)
    with pytest.raises(SafetyPause, match="source_memory"):
        guard.start()
    assert guard.thread is None
    guard.close()
    assert probe.closed


def test_stale_sample_and_original_deadline_fail_closed():
    now = [0]
    probe = Probe()
    guard = CatalogFinishGuard(probe, deadline=110, clock=lambda: now[0])
    assert guard._check()
    guard()
    now[0] = 6
    with pytest.raises(SafetyPause, match="health_monitor_stale"):
        guard()
    assert guard._check()
    now[0] = 110
    with pytest.raises(SafetyPause, match="catalog_part_deadline"):
        guard()
    guard.close()


def test_monitor_failure_is_latched_and_closes_client(monkeypatch):
    probe = Probe()
    guard = CatalogFinishGuard(probe, deadline=110, clock=lambda: 0)
    assert guard._check()
    monkeypatch.setattr(guard.stop, "wait", lambda _: False)
    probe.ok = False
    probe.last = {"reason": "source_receiver_health"}
    guard._monitor()
    probe.ok = True
    with pytest.raises(SafetyPause, match="source_receiver_health"):
        guard()
    assert probe.closed


def test_native_walk_interrupt_preserves_pause_reason_and_removes_handler(tmp_path):
    path = tmp_path / "isolated.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY, value TEXT UNIQUE)")
        db.executemany("INSERT INTO sample VALUES (?,?)", ((n, str(n)) for n in range(20000)))
        db.commit()
        calls = 0

        def checkpoint():
            nonlocal calls
            calls += 1
            if calls >= 2:
                raise SafetyPause("source_memory")

        with pytest.raises(SafetyPause, match="source_memory"):
            full_integrity(db, path, checkpoint)
        assert calls >= 2
        assert db.execute("SELECT count(*) FROM sample").fetchone()[0] == 20000
        full_integrity(db, path, lambda: None)


def test_real_corruption_cannot_pass_native_walk(tmp_path):
    path = tmp_path / "isolated.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY, value TEXT UNIQUE)")
        db.execute("INSERT INTO sample VALUES (1,'preserved')")
        db.commit()
    with path.open("r+b") as body:
        body.seek(4096)
        body.write(b"\xff")
    with sqlite3.connect(path) as db:
        with pytest.raises(sqlite3.DatabaseError):
            full_integrity(db, path, lambda: None)
