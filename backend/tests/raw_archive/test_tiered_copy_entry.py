"""The production copy entry retains source authority through tiered IO."""

import json
from contextlib import contextmanager

import pytest

from app.raw_archive import archive_copy_once as entry
from app.raw_archive import remote_lease
from app.raw_archive.health import SafetyPause
from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import execution, latency, runtime, schedule


@pytest.fixture
def guarded(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(entry, "guard", lambda: events.append("owner"))

    @contextmanager
    def lease():
        events.append("lease-open")
        try:
            yield lambda: events.append("source-check")
        finally:
            events.append("lease-close")

    monkeypatch.setattr(entry, "catalog_lease", lease)
    monkeypatch.setenv("RAW_ARCHIVE_MAX_SECONDS", "41")
    monkeypatch.setenv("TIERED_ARCHIVE_ENABLED", "true")
    monkeypatch.setenv("TIERED_ARCHIVE_ENVIRONMENT", "production")
    monkeypatch.setenv("TIERED_ARCHIVE_DELETE_ENABLED", "false")
    monkeypatch.setenv("TIERED_ARCHIVE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("TIERED_ARCHIVE_SCHEDULE_FILE", str(tmp_path / "schedule.json"))
    return events


def test_legacy_default_uses_explicit_probe_not_global_replacement(guarded, monkeypatch):
    class Probe:
        last = {}

        def __call__(self):
            guarded.append("health")
            return True

        def close(self):
            pass

    monkeypatch.setattr(entry.runner, "health_probe", lambda **_: Probe())
    factory = entry.runner.health_probe

    def run(*, health_factory):
        assert entry.runner.health_probe is factory
        assert health_factory()()
        return {"status": "complete", "deleted": 0}

    monkeypatch.setattr(entry.runner, "run", run)
    assert entry.run()["status"] == "complete"
    assert entry.runner.health_probe is factory
    assert guarded == [
        "owner",
        "lease-open",
        "source-check",
        "source-check",
        "health",
        "lease-close",
    ]
    assert not remote_lease.source_copy_only()


def test_tiered_uses_bounded_single_json_and_cleans_authority(guarded, monkeypatch, capsys):
    monkeypatch.setenv("RAW_ARCHIVE_COPY_MODE", "tiered-copy-only")
    monkeypatch.setenv("RAW_ARCHIVE_ENVIRONMENT", "production")
    monkeypatch.setenv("RAW_ARCHIVE_WORKER_ROLE", "archive")
    actual = execution.bounded_job

    @contextmanager
    def bounded(root, *, seconds, emit):
        assert seconds == 41 and emit is False
        with actual(root, seconds=seconds, emit=emit):
            yield

    def tick(path, *, seconds):
        assert seconds == 41 and str(path).endswith("schedule.json")
        assert remote_lease.source_copy_only()
        remote_lease.source_checkpoint()
        return {"published": 2, "evicted": 0}

    monkeypatch.setattr(execution, "bounded_job", bounded)
    monkeypatch.setattr(schedule, "tick", tick)
    assert entry.main() == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "status": "complete",
        "deleted": 0,
        "published": 2,
        "evicted": 0,
    }
    assert guarded[0] == "owner" and guarded[-1] == "lease-close"
    with pytest.raises(SafetyPause, match="source_lease_required"):
        remote_lease.source_checkpoint()


@pytest.mark.parametrize("mode,delete", [("other", "false"), ("tiered-copy-only", "true")])
def test_unsupported_or_deleting_mode_never_acquires_source(guarded, monkeypatch, mode, delete):
    monkeypatch.setenv("RAW_ARCHIVE_COPY_MODE", mode)
    monkeypatch.setenv("TIERED_ARCHIVE_DELETE_ENABLED", delete)
    with pytest.raises(ArchiveBlocked):
        entry.run()
    assert guarded == ["owner"]


def test_planning_checks_live_source_before_plan_or_state_io(monkeypatch, tmp_path):
    monkeypatch.setattr(schedule, "private_json", lambda *_: pytest.fail("No plan IO"))
    monkeypatch.setattr(schedule, "Ledger", lambda *_: pytest.fail("No state IO"))
    checks = [0]

    def check():
        checks[0] += 1
        if checks[0] > 1:
            raise SafetyPause("source_lease_lost")

    with remote_lease.bound_source_lease(check, copy_only=True):
        with pytest.raises(SafetyPause, match="source_lease_lost"):
            schedule.tick(tmp_path / "plan")
    assert not remote_lease.source_copy_only()


def test_runtime_checkpoint_checks_source_inside_health_throttle(monkeypatch, tmp_path):
    monkeypatch.setenv("TIERED_ARCHIVE_ENABLED", "true")
    monkeypatch.setenv("RAW_ARCHIVE_ENVIRONMENT", "staging")
    monkeypatch.setenv("TIERED_ARCHIVE_ENVIRONMENT", "staging")
    monkeypatch.setenv("TIERED_ARCHIVE_STATE_DIR", str(tmp_path))
    closed = []

    class Healthy:
        def __call__(self):
            return True

        def close(self):
            closed.append("health")

    class Destination:
        @contextmanager
        def session(self, *_args, **_kwargs):
            try:
                yield
            finally:
                closed.append("transport")

    monkeypatch.setattr(latency, "health_probe", lambda *_: Healthy())
    monkeypatch.setattr(entry.runner, "destination_from_environment", lambda: Destination())
    lost = [False]

    def check():
        if lost[0]:
            raise SafetyPause("source_lease_lost")

    with remote_lease.bound_source_lease(check, copy_only=True):
        with pytest.raises(SafetyPause, match="source_lease_lost"):
            with runtime.worker() as (_, _, checkpoint):
                checkpoint()
                lost[0] = True
                checkpoint()
    assert closed == ["transport", "health"]
    assert not remote_lease.source_copy_only()


def test_copy_authority_forbids_deleting_before_state_io(monkeypatch):
    monkeypatch.setattr(runtime, "state_root", lambda: pytest.fail("No state IO"))
    with remote_lease.bound_source_lease(lambda: None, copy_only=True):
        with pytest.raises(ArchiveBlocked, match="forbids deletion"):
            with runtime.worker(deleting=True):
                pytest.fail("No deletion")


def test_lost_lease_returns_75_one_json_and_releases_scope(guarded, monkeypatch, capsys):
    monkeypatch.setenv("RAW_ARCHIVE_COPY_MODE", "tiered-copy-only")

    def tick(*_args, **_kwargs):
        raise SafetyPause("source_lease_lost")

    monkeypatch.setattr(schedule, "tick", tick)
    assert entry.main() == 75
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["reason"] == "source_lease_lost"
    assert guarded[-1] == "lease-close" and not remote_lease.source_copy_only()


def test_real_deadline_returns_75_without_duplicate_output(guarded, monkeypatch, capsys):
    import time

    monkeypatch.setenv("RAW_ARCHIVE_COPY_MODE", "tiered-copy-only")
    monkeypatch.setenv("RAW_ARCHIVE_MAX_SECONDS", "1")
    monkeypatch.setattr(schedule, "tick", lambda *_args, **_kwargs: time.sleep(2))
    assert entry.main() == 75
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["reason"] == "runtime_budget"
    assert guarded[-1] == "lease-close" and not remote_lease.source_copy_only()


@pytest.mark.parametrize("seconds", [0, 76, 999])
def test_real_owner_guard_rejects_over_budget_before_files(monkeypatch, seconds):
    from types import SimpleNamespace

    monkeypatch.setattr(entry.os, "uname", lambda: SimpleNamespace(nodename="ubuntu-4gb-fsn1-1"))
    monkeypatch.setattr(entry.os, "getuid", lambda: 42)
    monkeypatch.setattr(entry.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=42))
    flags = {
        "RAW_ARCHIVE_WORKER_ROLE": "archive",
        "RAW_ARCHIVE_ENVIRONMENT": "production",
        "RAW_ARCHIVE_ENABLED": "true",
        "RAW_ARCHIVE_DELETE_ENABLED": "false",
        "RAW_ARCHIVE_LEGACY_NULL_ACCEPTED": "false",
        "RETENTION_ENABLED": "false",
        "DIRECT_RETENTION_ENABLED": "false",
        "VISUAL_PRUNE_ENABLED": "false",
        "RAW_ARCHIVE_MAX_SECONDS": str(seconds),
    }
    for key, value in flags.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ArchiveBlocked, match="bounded copy-only"):
        entry.guard()
