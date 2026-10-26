"""Fault injection verifies sole-writer safety, retained proofs and receiver guards."""

import pytest

from app.raw_archive.cutover import Cutover
from app.raw_archive.policy import ArchiveBlocked

COHORT = "a" * 24


class Adapter:
    def __init__(self, fail=None, stopped=True, accepted=False):
        self.events = []
        self.fail, self.stopped, self.accepted = fail, stopped, accepted

    def __getattr__(self, name):
        def call(*args):
            self.events.append(name)
            if name == self.fail:
                raise RuntimeError("Injected response loss")
            if name == "preflight":
                return "receiver-starts-and-restarts"
            if name == "cold_snapshot":
                return {"sha256": "b" * 64, "counts": {"wav_file": 42}}
            if name == "transfer_restore":
                return {"cohort": args[0], "snapshot": args[1]}
            if name == "rehearse":
                return {
                    "cohort": COHORT,
                    "receiver_continuity": True,
                    "proxy_rollback_verified": True,
                    "public_writes_fenced": True,
                    "source_snapshot_reconciled": True,
                }
            if name == "target_writers_stopped":
                return self.stopped
            if name == "accepted_owner":
                return self.accepted
            return None

        return call


def test_rehearsal_restores_without_publishing(tmp_path):
    adapter = Adapter()
    result = Cutover(adapter, tmp_path / "fresh", COHORT).run()
    assert result["published"] is False
    assert "publish" not in adapter.events and "commit_owner" not in adapter.events
    assert adapter.events.index("arm_watchdog") < adapter.events.index("pause_source")
    assert adapter.events.index("stop_target") < adapter.events.index("resume_source")
    assert (tmp_path / "fresh/source-snapshot.json").is_file()


@pytest.mark.parametrize(
    "operation", ["pause_source", "transfer_restore", "start_target", "publish"]
)
def test_lost_remote_response_stops_target_before_resuming_source(tmp_path, operation):
    adapter = Adapter(fail=operation)
    with pytest.raises(RuntimeError):
        Cutover(adapter, tmp_path / "fresh", COHORT).run(publish=True)
    assert adapter.events.index("stop_target") < adapter.events.index("resume_source")


def test_unconfirmed_target_never_resumes_source(tmp_path):
    adapter = Adapter(fail="start_target", stopped=False)
    with pytest.raises(ArchiveBlocked, match="unknown"):
        Cutover(adapter, tmp_path / "fresh", COHORT).run()
    assert "resume_source" not in adapter.events
    assert (tmp_path / "fresh/rollback-blocked.json").is_file()


def test_existing_evidence_rejected_before_side_effects(tmp_path):
    adapter = Adapter()
    with pytest.raises(ArchiveBlocked, match="existing evidence"):
        Cutover(adapter, tmp_path, COHORT).run()
    assert not adapter.events


def test_accepted_remote_owner_never_creates_second_source_writer(tmp_path):
    adapter = Adapter(fail="commit_owner", accepted=True)
    with pytest.raises(RuntimeError):
        Cutover(adapter, tmp_path / "fresh", COHORT).run(publish=True)
    assert "resume_source" not in adapter.events and "stop_target" not in adapter.events
    assert (tmp_path / "fresh/rollback-withheld.json").is_file()


def test_expired_budget_prevents_source_pause(tmp_path):
    adapter = Adapter()
    ticks = iter([0, 721])
    with pytest.raises(ArchiveBlocked, match="time budget"):
        Cutover(adapter, tmp_path / "fresh", COHORT, clock=lambda: next(ticks)).run()
    assert "pause_source" not in adapter.events


def test_wrong_target_snapshot_rolls_back(tmp_path):
    adapter = Adapter()
    adapter.transfer_restore = lambda *_: {"cohort": "c" * 24, "snapshot": {}}
    with pytest.raises(ArchiveBlocked, match="different source"):
        Cutover(adapter, tmp_path / "fresh", COHORT).run()
    assert "start_target" not in adapter.events
    assert "resume_source" in adapter.events


def test_incomplete_rehearsal_cannot_publish(tmp_path):
    adapter = Adapter()
    adapter.rehearse = lambda _: {"cohort": COHORT, "receiver_continuity": True}
    with pytest.raises(ArchiveBlocked, match="incomplete"):
        Cutover(adapter, tmp_path / "fresh", COHORT).run(publish=True)
    assert "publish" not in adapter.events
