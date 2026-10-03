import json
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.raw_archive.daily import Limits, Queue
from app.raw_archive.health import HealthProbe, SafetyPause, await_headroom
from app.raw_archive.policy import Ledger
from app.raw_archive.telemetry import Metrics


def test_http_cache_preserves_immediate_memory_and_load_guards(tmp_path, monkeypatch):
    state = {"time": 0, "memory": 1024 * 1024, "load": 1, "http": 200}
    calls = []
    original = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda path: f"MemAvailable: {state['memory']} kB"
        if str(path) == "/proc/meminfo"
        else original(path),
    )
    monkeypatch.setattr("app.raw_archive.health.os.getloadavg", lambda: (state["load"], 0, 0))

    def stream(*args):
        calls.append(args)
        return nullcontext(SimpleNamespace(status_code=state["http"]))

    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: nullcontext(SimpleNamespace(stream=stream))
    )
    probe = HealthProbe(
        ["http://127.0.0.1/a", "http://127.0.0.1/b"],
        [tmp_path],
        128,
        2.4,
        clock=lambda: state["time"],
    )
    try:
        assert probe()
        for _ in range(30):
            assert probe()
        assert len(calls) == 2
        state["memory"] = 64 * 1024
        assert not probe() and probe.last["reason"] == "memory"
        state.update(memory=1024 * 1024, load=3)
        assert not probe() and probe.last["reason"] == "host_load"
        state.update(time=5, load=1, http=503)
        assert not probe() and probe.last["reason"] == "receiver_health"
        assert not probe()  # A failed health response must never be cached as healthy.
    finally:
        probe.close()


def test_load_wait_requires_three_healthy_samples_and_is_bounded():
    clock = [0]
    metrics = Metrics()

    class Probe:
        last = {"reason": "host_load"}

        def __call__(self):
            return clock[0] >= 20

    await_headroom(
        Probe(),
        deadline=500,
        paused=lambda: False,
        metrics=metrics,
        clock=lambda: clock[0],
        sleep=lambda secs: clock.__setitem__(0, clock[0] + secs),
    )
    assert clock[0] == 40

    class Overloaded:
        last = {"reason": "host_load"}

        def __call__(self):
            return False

    with pytest.raises(SafetyPause) as error:
        await_headroom(
            Overloaded(),
            deadline=500,
            paused=lambda: False,
            clock=lambda: clock[0],
            sleep=lambda secs: clock.__setitem__(0, clock[0] + secs),
        )
    assert error.value.code == "host_load_timeout" and clock[0] == 160

    class MemoryLow(Overloaded):
        last = {"reason": "memory"}

    with pytest.raises(SafetyPause) as error:
        await_headroom(
            MemoryLow(),
            deadline=500,
            paused=lambda: False,
            clock=lambda: clock[0],
            sleep=lambda _: pytest.fail("Must not wait"),
        )
    assert error.value.code == "memory"


def test_completed_reference_defers_recheck_but_changed_source_requeues(tmp_path):
    ledger = Ledger(tmp_path)
    try:
        queue = Queue(ledger, {})
        ref = json.dumps(["a"])

        def discover(fingerprint, at):
            queue.enqueue_page(
                "gateway", [["a"]], finished=True, fingerprints={ref: fingerprint}, now_epoch=at
            )

        discover("first", 10)
        queue.attempted("gateway", ref, completed=True, next_check_at=100)
        discover("first", 50)
        assert queue.remaining() == 0
        discover("changed", 60)
        assert queue.remaining() == 1
        queue.attempted("gateway", ref, completed=True, next_check_at=100)
        queue.activate_due(101, 5)
        assert queue.remaining() == 1
    finally:
        ledger.close()


def test_recent_scan_cursor_survives_small_runs(world):
    config, catalogs, _, _, add, execute = world
    for n in range(1, 31):
        add("gateway", n)
    seen = []
    original = catalogs["gateway"].recent_page

    def page(cursor, after, before, count):
        seen.append(cursor)
        return original(cursor, after, before, count)

    catalogs["gateway"].recent_page = page
    for _ in range(3):
        execute(cfg=replace(config, max_files=1), limits=Limits(references=8, page_size=2))
    assert seen[:3] == [None, ["0002"], ["0004"]]


def test_adaptive_transfer_budget_is_not_a_ram_allocation():
    import runpy

    script = Path(__file__).resolve().parents[3] / "deploy/raw-archive/adaptive-copy.py"
    plan = runpy.run_path(str(script))["plan"]
    result = plan(512 * 1024, 3, 1024**3)
    assert result["copy_budget_bytes"] == 1024**3
    assert result["reserve_mib"] == 128
    assert result["maximum_host_load"] == pytest.approx(2.4)


def test_legacy_queue_upgrade_preserves_pending_work(tmp_path):
    ledger = Ledger(tmp_path)
    try:
        ledger.db.execute("""CREATE TABLE backup_work (kind TEXT, reference TEXT,
            active INTEGER DEFAULT 1, attempt REAL DEFAULT 0, failure TEXT,
            priority INTEGER DEFAULT 20, PRIMARY KEY(kind,reference))""")
        ledger.db.execute(
            "INSERT INTO backup_work(kind,reference) VALUES (?,?)",
            ("gateway", json.dumps(["legacy"])),
        )
        ledger.db.commit()
        queue = Queue(ledger, {})
        assert queue.remaining() == 1
        assert queue.pending(1) == [("gateway", json.dumps(["legacy"]))]
        columns = {row[1] for row in ledger.db.execute("PRAGMA table_info(backup_work)")}
        assert {"fingerprint", "next_check_at"} <= columns
    finally:
        ledger.close()
