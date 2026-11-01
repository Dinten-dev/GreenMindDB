from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.snapshot_api import managed_snapshot_plan

NOW = datetime(2026, 11, 1, tzinfo=UTC)


def snapshot(number):
    return {
        "id": number,
        "storage_box": 12,
        "name": f"snapshot-{number}",
        "created": (NOW - timedelta(hours=number + 1)).isoformat(),
    }


def record(row):
    return {
        "snapshot": deepcopy(row),
        "proof": {
            "snapshot_id": row["id"],
            "inventory_sha256": "a" * 64,
            "objects": 2,
            "bytes": 42,
            "checked_at": NOW.isoformat(),
        },
    }


def plan(snapshots, records, capacity=10):
    return managed_snapshot_plan(snapshots, records, box_id=12, capacity=capacity, now=NOW)


def test_three_slots_required_and_manual_snapshots_never_selected():
    manual = [snapshot(n) for n in range(10, 17)]
    assert plan(manual, []) == {"action": "create", "snapshot_id": None}
    with pytest.raises(ArchiveBlocked, match="positions"):
        plan(manual + [snapshot(17)], [])
    owned = [snapshot(n) for n in range(1, 4)]
    assert plan(manual + owned, [record(row) for row in owned]) == {
        "action": "rotate",
        "snapshot_id": 3,
    }


def test_each_new_snapshot_needs_remaining_capacity():
    manual = [snapshot(n) for n in range(10, 17)]
    owned = [snapshot(1), snapshot(2)]
    assert plan(manual + owned, [record(row) for row in owned])["action"] == "create"
    with pytest.raises(ArchiveBlocked, match="positions"):
        plan(manual + owned + [snapshot(20)], [record(row) for row in owned])


@pytest.mark.parametrize("change", ["absent", "identity", "proof", "future", "duplicate"])
def test_incomplete_or_changed_evidence_never_authorizes_rotation(change):
    rows = [snapshot(n) for n in range(1, 4)]
    records = [record(row) for row in rows]
    if change == "absent":
        rows.pop()
    elif change == "identity":
        rows[0]["name"] = "changed"
    elif change == "proof":
        records[0]["proof"]["objects"] = 0
    elif change == "future":
        records[0]["proof"]["checked_at"] = (NOW + timedelta(seconds=1)).isoformat()
    else:
        records[1] = deepcopy(records[0])
    with pytest.raises(ArchiveBlocked):
        plan(rows, records)


def test_production_schedule_requires_both_backups_before_work(tmp_path, monkeypatch):
    import json

    from app.tiered_archive import runtime, schedule

    monkeypatch.setattr(schedule, "source_checkpoint", lambda: None)
    monkeypatch.setattr(schedule, "environment", lambda: "production")
    monkeypatch.setattr(
        runtime, "latest_snapshot", lambda: pytest.fail("Unexpected provider access")
    )
    path = tmp_path / "plan.json"
    path.write_text(
        json.dumps({"schema": 1, "environment": "production", "scheduled_snapshots": True})
    )
    path.chmod(0o600)
    with pytest.raises(ArchiveBlocked, match="require both backups"):
        schedule.tick(path)


def test_latest_snapshot_cannot_bypass_production_guard(monkeypatch):
    from app.tiered_archive import runtime

    monkeypatch.setattr(runtime, "environment", lambda: "production")
    with pytest.raises(ArchiveBlocked, match="not a managed production snapshot"):
        runtime.latest_snapshot()


def test_production_schedule_runs_both_backups_before_managed_snapshot(tmp_path, monkeypatch):
    import json
    from contextlib import contextmanager

    from app.tiered_archive import registry_backup, runtime, schedule
    from app.tiered_archive.ledger import Ledger

    root = tmp_path / "state"
    calls = []
    monkeypatch.setattr(schedule, "source_checkpoint", lambda: None)
    monkeypatch.setattr(schedule, "environment", lambda: "production")
    monkeypatch.setattr(schedule, "state_root", lambda: root)
    monkeypatch.setattr(runtime, "state_root", lambda: root)
    monkeypatch.setattr(runtime, "backup", lambda source: calls.append(source))
    monkeypatch.setattr(runtime, "managed_snapshot", lambda: calls.append("snapshot") or 100)
    monkeypatch.setattr(registry_backup, "backup", lambda *_a, **_k: {"status": "empty"})

    class Healthy:
        def __call__(self):
            return True

        def close(self):
            pass

    @contextmanager
    def worker():
        ledger = Ledger(root)
        try:
            yield ledger, None, lambda: None
        finally:
            ledger.close()

    monkeypatch.setattr(schedule, "health_probe", lambda *_: Healthy())
    monkeypatch.setattr(runtime, "worker", worker)
    path = tmp_path / "plan.json"
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "environment": "production",
                "scheduled_snapshots": True,
                "backups": ["gateway", "direct"],
            }
        )
    )
    path.chmod(0o600)
    schedule.tick(path, clock=lambda: NOW)
    assert calls == ["gateway", "direct", "snapshot"]
