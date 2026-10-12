"""Archive exports release resources and preserve resumable progress."""

from contextlib import contextmanager
from pathlib import Path

import pytest

from app.raw_archive import archive_export_once as export
from app.raw_archive.health import SafetyPause
from app.raw_archive.policy import ArchiveBlocked


def test_guard_denial_opens_no_jobs_or_source_lease(monkeypatch):
    monkeypatch.setattr(export, "guard", lambda: (_ for _ in ()).throw(ArchiveBlocked("guard")))
    monkeypatch.setattr(export.worker, "root", lambda: pytest.fail("no job connection"))
    monkeypatch.setattr(export, "catalog_lease", lambda: pytest.fail("no source lease"))
    with pytest.raises(ArchiveBlocked):
        export.run()


@pytest.mark.parametrize("reason", ["source_lease_lost", "source_memory"])
def test_pause_keeps_original_worker_resume_path_and_closes_probe(monkeypatch, reason):
    events = []
    monkeypatch.setattr(export, "guard", lambda: None)
    monkeypatch.setattr(export.worker, "root", lambda: Path("/private-fixture"))

    class DB:
        def execute(self, *args):
            return self

        def fetchone(self):
            return (1,)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(export.worker, "connect", lambda _: DB())

    @contextmanager
    def lease(*args):
        def check():
            if reason == "source_lease_lost":
                raise SafetyPause(reason)

        yield check

    monkeypatch.setattr(export, "catalog_lease", lease)
    monkeypatch.setattr("app.raw_archive.coordination.lease", lease)

    class Probe:
        last = {"reason": reason}

        def __call__(self):
            return False

        def close(self):
            events.append("closed")

    monkeypatch.setattr(export, "health_probe", Probe)

    def original(_):
        events.append("original")

    monkeypatch.setattr(export.worker, "headroom", original)

    def run():
        with pytest.raises(export.worker.CapacityPause, match=reason):
            export.worker.headroom(Path("/private-fixture"))
        return {"status": "paused_for_host_load"}

    monkeypatch.setattr(export.worker, "_run_once", run)
    assert export.run()["status"] == "paused_for_host_load"
    assert events == ["closed"] and export.worker.headroom is original
