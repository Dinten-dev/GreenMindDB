"""Real jobs locks and fake source transport exercise lifecycle ordering."""

import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.raw_archive import coordination, remote_lease, runner
from app.raw_archive.health import SafetyPause
from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import __main__ as cli
from app.tiered_archive import latency, runtime


@pytest.fixture
def setup(tmp_path, monkeypatch):
    coord = tmp_path / "coord"
    coord.mkdir(mode=0o700)
    monkeypatch.setenv("RAW_ARCHIVE_COORDINATION_DIR", str(coord))
    monkeypatch.setenv("RAW_ARCHIVE_WORKER_ROLE", "archive")
    monkeypatch.setenv("RAW_ARCHIVE_ENVIRONMENT", "production")
    monkeypatch.setenv("TIERED_ARCHIVE_ENABLED", "true")
    monkeypatch.setattr(runtime, "environment", lambda: "production")
    monkeypatch.setattr(runtime, "state_root", lambda: tmp_path / "state")
    return coord


@pytest.mark.parametrize("job_kind", ["copy", "catalog"])
def test_worker_reuses_archive_fd_after_source_lease(setup, monkeypatch, job_kind):
    events = []
    held = []

    @contextmanager
    def source():
        path, info = coordination.bound_job_lock()
        held.append((path, info.st_dev, info.st_ino, coordination._BOUND_JOB_LOCK.get()[0]))
        events.append("source-open")

        def check():
            coordination.bound_job_lock()
            events.append("source-check")

        try:
            yield check
        finally:
            coordination.bound_job_lock()
            events.append("source-close")

    class Health:
        def __call__(self):
            return True

        def close(self):
            events.append("health-close")

    class Transport:
        @contextmanager
        def session(self, *_args, **_kwargs):
            path, info = coordination.bound_job_lock()
            assert (path, info.st_dev, info.st_ino, coordination._BOUND_JOB_LOCK.get()[0]) == held[0]
            events.append("worker-enter")
            yield
            events.append("worker-exit")

    monkeypatch.setattr(remote_lease, "catalog_lease", source)
    monkeypatch.setattr(latency, "health_probe", lambda *_: Health())
    monkeypatch.setattr(runner, "destination_from_environment", lambda: Transport())

    def run():
        with runtime.worker(coordination_kind=job_kind) as (_ledger, _store, checkpoint):
            checkpoint()
            events.append("callback")
        return "ok"

    assert cli.with_source_catalogue_lease(run, job_kind=job_kind) == "ok"
    assert events.index("source-open") < events.index("worker-enter") < events.index("callback")
    assert events.index("worker-exit") < events.index("source-close")
    with pytest.raises(ArchiveBlocked, match="jobs lock required"):
        coordination.bound_job_lock()
    with coordination.lease("copy"):
        pass


@pytest.mark.parametrize("job_kind", ["copy", "catalog"])
def test_busy_archive_lock_prevents_source_connection(setup, monkeypatch, job_kind):
    opened = []

    @contextmanager
    def source():
        opened.append(True)
        yield lambda: None

    monkeypatch.setattr(remote_lease, "catalog_lease", source)
    with (setup / "jobs.lock").open("a") as competitor:
        (setup / "jobs.lock").chmod(0o600)
        fcntl.flock(competitor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(SafetyPause, match="archive_job_busy"):
            cli.with_source_catalogue_lease(lambda: pytest.fail("busy callback ran"), job_kind=job_kind)
        fcntl.flock(competitor, fcntl.LOCK_UN)
    assert opened == []


def test_snapshot_cycle_cli_runs_inside_both_live_leases(setup, monkeypatch, capsys):
    @contextmanager
    def source():
        coordination.bound_job_lock()
        yield lambda: coordination.bound_job_lock()

    def cycle():
        coordination.bound_job_lock()
        remote_lease.require_bound_source_lease()
        return {"status": "READBACK_PENDING", "snapshot_id": 101}

    monkeypatch.setattr(remote_lease, "catalog_lease", source)
    monkeypatch.setattr(runtime, "managed_snapshot", cycle)
    monkeypatch.setattr("sys.argv", ["tiered-archive", "snapshot-cycle"])
    cli.main()
    assert '"status": "READBACK_PENDING"' in capsys.readouterr().out
    with pytest.raises(ArchiveBlocked, match="jobs lock required"):
        coordination.bound_job_lock()


@pytest.mark.parametrize("failure", ["source", "callback"])
def test_source_or_callback_error_releases_both_authorities(setup, monkeypatch, failure):
    events = []

    @contextmanager
    def source():
        coordination.bound_job_lock()
        events.append("open")
        if failure == "source":
            raise ValueError("source failed")
        try:
            yield lambda: None
        finally:
            coordination.bound_job_lock()
            events.append("closed")

    monkeypatch.setattr(remote_lease, "catalog_lease", source)

    def callback():
        raise ValueError("callback failed")

    with pytest.raises(ValueError, match="failed"):
        cli.with_source_catalogue_lease(callback)
    assert events == (["open"] if failure == "source" else ["open", "closed"])
    with pytest.raises(ArchiveBlocked, match="jobs lock required"):
        coordination.bound_job_lock()
    with coordination.lease("copy"):
        pass
    with pytest.raises(SafetyPause, match="source_lease_required"):
        remote_lease.source_checkpoint()


def test_reuse_rejects_other_kind_changed_directory_and_dead_fd(setup, monkeypatch, tmp_path):
    other = tmp_path / "other"
    other.mkdir(mode=0o700)
    with coordination.lease("copy"):
        with pytest.raises(ArchiveBlocked, match="scope differs"):
            with coordination.reuse_or_lease("export"):
                pass
        monkeypatch.setenv("RAW_ARCHIVE_COORDINATION_DIR", str(other))
        with pytest.raises(ArchiveBlocked, match="scope differs"):
            with coordination.reuse_or_lease("copy"):
                pass
        monkeypatch.setenv("RAW_ARCHIVE_COORDINATION_DIR", str(setup))
        descriptor = coordination._BOUND_JOB_LOCK.get()[0]
        process_id = coordination.os.getpid()
        with monkeypatch.context() as patch:
            patch.setattr(coordination.os, "getpid", lambda: process_id + 1)
            with pytest.raises(ArchiveBlocked, match="another execution"):
                with coordination.reuse_or_lease("copy"):
                    pass
        original = coordination.os.fstat
        with monkeypatch.context() as patch:
            def fail_for_held(fd):
                if fd == descriptor:
                    raise OSError("closed")
                return original(fd)

            patch.setattr(coordination.os, "fstat", fail_for_held)
            with pytest.raises(ArchiveBlocked, match="lock unavailable"):
                with coordination.reuse_or_lease("copy"):
                    pass


def test_missing_archive_coordination_never_opens_source(setup, monkeypatch):
    opened = []

    @contextmanager
    def source():
        opened.append(True)
        yield lambda: None

    monkeypatch.setattr(remote_lease, "catalog_lease", source)
    monkeypatch.delenv("RAW_ARCHIVE_COORDINATION_DIR")
    with pytest.raises(ArchiveBlocked, match="jobs lock required"):
        cli.with_source_catalogue_lease(lambda: pytest.fail("uncoordinated callback ran"))
    assert opened == []


def test_linux_requires_owned_full_file_flock(setup, monkeypatch):
    original_read = Path.read_text
    with coordination.lease("copy"):
        descriptor, _path, pid, _thread, _kind = coordination._BOUND_JOB_LOCK.get()
        info = os.fstat(descriptor)
        address = f"{os.major(info.st_dev):x}:{os.minor(info.st_dev):x}:{info.st_ino}"
        row = f"1: FLOCK ADVISORY WRITE {pid} {address} 0 EOF\n"
        with monkeypatch.context() as patch:
            patch.setattr(coordination.sys, "platform", "linux")
            patch.setattr(Path, "read_text", lambda path, *a, **kw: row if str(path) == "/proc/locks" else original_read(path, *a, **kw))
            with coordination.reuse_or_lease("copy"):
                pass
            row = f"1: FLOCK ADVISORY WRITE {pid + 1} {address} 0 EOF\n"
            with pytest.raises(ArchiveBlocked, match="no longer held"):
                coordination.bound_job_lock()


def test_borrowed_copy_lock_respects_new_higher_priority_request(setup):
    with coordination.lease("copy"):
        coordination.request(setup, "export", time.time() + 60)
        with pytest.raises(SafetyPause, match="archive_job_yield"):
            with coordination.reuse_or_lease("copy"):
                pass
    with coordination.lease("export"):
        pass


def test_catalogue_request_yields_copy_without_opening_source(setup, monkeypatch):
    import json
    opened = []
    @contextmanager
    def source():
        opened.append(True)
        yield lambda: None
    monkeypatch.setattr(remote_lease, "catalog_lease", source)
    with coordination.lease("copy"):
        # Another execution cannot borrow the owner's context.
        with monkeypatch.context():
            token = coordination._BOUND_JOB_LOCK.set(None)
            try:
                with pytest.raises(SafetyPause, match="archive_job_busy"):
                    cli.with_source_catalogue_lease(lambda: None, job_kind="catalog")
            finally:
                coordination._BOUND_JOB_LOCK.reset(token)
        assert json.loads((setup/"catalog.request").read_text())["expires"] > time.time()
        with pytest.raises(SafetyPause, match="archive_job_yield"):
            with coordination.reuse_or_lease("copy"):
                pass
    assert opened == []
    assert cli.with_source_catalogue_lease(lambda: "ok", job_kind="catalog") == "ok"
    assert not coordination.yield_requested()


def test_catalogue_worker_cannot_enable_deletion(setup):
    with pytest.raises(ArchiveBlocked, match="coordination kind"):
        with runtime.worker(deleting=True, coordination_kind="catalog"):
            pytest.fail("deletion entered")
