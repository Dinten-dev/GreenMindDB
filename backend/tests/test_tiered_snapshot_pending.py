"""A new upload waits for a covering snapshot; malformed proofs remain failures."""

import json
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import runtime, schedule
from app.tiered_archive.pipeline import SnapshotPending, publish, verify_snapshot
from tests.test_tiered_archive import AT, Provider, bundle  # noqa: F401


@pytest.fixture
def waiting(bundle, tmp_path, monkeypatch):  # noqa: F811 - Pytest injects the imported shared fixture.
    candidate, path, metadata, ledger, box, _ = bundle
    published = AT + timedelta(days=2)
    now = published + timedelta(days=1)
    publish(candidate, [(path, metadata)], box, ledger, checkpoint=lambda: None, now=published)
    selected = [AT]

    class Healthy:
        def __call__(self):
            return True

        def close(self):
            pass

    @contextmanager
    def worker():
        yield ledger, box, lambda: None

    monkeypatch.setattr(schedule, "environment", lambda: "staging")
    monkeypatch.setattr(schedule, "state_root", lambda: ledger.root)
    monkeypatch.setattr(schedule, "health_probe", lambda _: Healthy())
    monkeypatch.setattr(runtime, "state_root", lambda: ledger.root)
    monkeypatch.setattr(runtime, "worker", worker)
    monkeypatch.setattr(
        runtime,
        "wav_source",
        lambda *_: SimpleNamespace(catalog=SimpleNamespace(page=lambda *_: [])),
    )
    monkeypatch.setattr(
        runtime,
        "verify",
        lambda identity, snapshot_id: verify_snapshot(
            candidate,
            box,
            ledger,
            Provider(selected[0]),
            snapshot_id,
            checkpoint=lambda: None,
            now=now,
        ),
    )
    monkeypatch.setenv("TIERED_ARCHIVE_DELETE_ENABLED", "false")
    plan = tmp_path / "schedule.json"
    plan.write_text(
        json.dumps(
            {"schema": 1, "environment": "staging", "wav_sources": ["gateway"], "snapshot_id": 3}
        )
    )
    plan.chmod(0o600)
    return SimpleNamespace(
        candidate=candidate,
        ledger=ledger,
        box=box,
        selected=selected,
        now=now,
        tick=lambda: schedule.tick(plan, clock=lambda: now),
    )


def test_pending_snapshot_does_not_pause_or_create_verification(waiting):
    h = waiting
    result = h.tick()
    assert result["deferred"] == 1
    assert result["verified"] == result["evicted"] == result["blocked"] == 0
    receipt = h.ledger.get(h.candidate.identity)
    assert receipt["state"] == "published" and "first_verified_at" not in receipt
    assert h.box.gets == 0
    assert not (h.ledger.root / "control/PAUSE").exists()


def test_later_covering_snapshot_starts_reserve_only_after_full_check(waiting):
    h = waiting
    assert h.tick()["deferred"] == 1
    h.selected[0] = h.now
    assert h.tick()["verified"] == 1
    receipt = h.ledger.get(h.candidate.identity)
    assert receipt["state"] == "verified"
    assert receipt["first_verified_at"] == h.now.isoformat()
    assert h.box.gets == 2  # Full manifest plus complete Parquet.


def test_future_snapshot_is_failure_not_pending(waiting):
    h = waiting
    h.selected[0] = h.now + timedelta(seconds=1)
    with pytest.raises(ArchiveBlocked, match="future-dated") as failure:
        h.tick()
    assert not isinstance(failure.value, SnapshotPending)
    assert h.ledger.get(h.candidate.identity)["state"] == "published"
    assert h.box.gets == 0


def test_covering_but_corrupt_snapshot_still_fails_staging(waiting):
    h = waiting
    h.selected[0] = h.now
    receipt = h.ledger.get(h.candidate.identity)
    (h.box.root / receipt["files"][0]["key"]).write_bytes(b"corrupt")
    with pytest.raises(ArchiveBlocked) as failure:
        h.tick()
    assert not isinstance(failure.value, SnapshotPending)
    assert h.ledger.get(h.candidate.identity)["state"] == "published"
