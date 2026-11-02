"""Real local archive locks and worker; Source/transport never leave process."""
from contextlib import contextmanager

import pytest

from app.raw_archive import coordination, remote_lease, runner
from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import __main__ as cli
from app.tiered_archive import latency, runtime


@pytest.fixture
def isolated_backup(tmp_path, monkeypatch):
    coord = tmp_path / "coord"
    coord.mkdir(mode=0o700)
    for key, value in {
        "RAW_ARCHIVE_COORDINATION_DIR": str(coord),
        "RAW_ARCHIVE_WORKER_ROLE": "archive",
        "RAW_ARCHIVE_ENVIRONMENT": "production",
        "TIERED_ARCHIVE_ENABLED": "true",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("TIERED_ARCHIVE_GATEWAY_BACKUP_DATABASE_URL", raising=False)
    monkeypatch.delenv("TIERED_ARCHIVE_PARENT_SNAPSHOT_ENABLED", raising=False)
    monkeypatch.setattr(runtime, "environment", lambda: "production")
    monkeypatch.setattr(runtime, "state_root", lambda: tmp_path / "state")
    events = []

    @contextmanager
    def source():
        coordination.bound_job_lock()
        events.append("source-open")
        try:
            yield lambda: coordination.bound_job_lock()
        finally:
            events.append("source-close")

    class Health:
        def __call__(self):
            return True

        def close(self):
            pass

    class Transport:
        @contextmanager
        def session(self, *_args, **_kwargs):
            coordination.bound_job_lock()
            remote_lease.require_bound_source_lease()
            events.append("worker-entered")
            yield

    monkeypatch.setattr(remote_lease, "catalog_lease", source)
    monkeypatch.setattr(latency, "health_probe", lambda *_: Health())
    monkeypatch.setattr(runner, "destination_from_environment", lambda: Transport())
    return events


@pytest.mark.parametrize("kind", ["copy", "catalog"])
def test_backup_reuses_matching_outer_lock_and_stops_before_database(isolated_backup, kind):
    def call_backup():
        with pytest.raises(KeyError, match="TIERED_ARCHIVE_GATEWAY_BACKUP_DATABASE_URL"):
            if kind == "copy":
                runtime.backup("gateway")
            else:
                runtime.backup("gateway", coordination_kind="catalog")

    cli.with_source_catalogue_lease(call_backup, job_kind=kind)
    assert isolated_backup == ["source-open", "worker-entered", "source-close"]
    with pytest.raises(ArchiveBlocked, match="jobs lock required"):
        coordination.bound_job_lock()


def test_catalog_outer_rejects_legacy_inner_copy(isolated_backup):
    with pytest.raises(ArchiveBlocked, match="scope differs"):
        cli.with_source_catalogue_lease(lambda: runtime.backup("gateway"), job_kind="catalog")
    assert isolated_backup == ["source-open", "source-close"]


@pytest.mark.parametrize("kind", ["export", "invalid", None, []])
def test_backup_rejects_invalid_coordination_kind_before_worker(isolated_backup, kind):
    with pytest.raises(ArchiveBlocked, match="Unsupported backup coordination kind"):
        runtime.backup("gateway", coordination_kind=kind)
    assert isolated_backup == []
