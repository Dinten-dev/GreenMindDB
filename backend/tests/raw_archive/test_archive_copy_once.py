"""No archive writer without fixed accepted ownership and a live source lease."""

from contextlib import contextmanager

import pytest

from app.raw_archive import archive_copy_once as copier
from app.raw_archive.health import SafetyPause
from app.raw_archive.policy import ArchiveBlocked


@pytest.mark.parametrize(
    "field,value",
    [
        ("owner", "source"),
        ("accepted", False),
        ("cohort", "../other"),
        ("source_journal_sha256", []),
        ("environment", "staging"),
    ],
)
def test_unaccepted_or_wrong_owner_rejected(field, value):
    owner = {
        "schema": 1,
        "owner": "archive",
        "accepted": True,
        "environment": "production",
        "cohort": "a" * 24,
        "source_journal_sha256": "b" * 64,
    }
    owner[field] = value
    with pytest.raises(ArchiveBlocked):
        copier.validate_owner(owner)


def test_preflight_blocks_every_connection(monkeypatch):
    monkeypatch.setattr(copier, "guard", lambda: (_ for _ in ()).throw(ArchiveBlocked("ownership")))
    monkeypatch.setattr(copier, "catalog_lease", lambda: pytest.fail("No source lease"))
    monkeypatch.setattr(copier.runner, "run", lambda: pytest.fail("No copy"))
    with pytest.raises(ArchiveBlocked):
        copier.run()


def test_source_lease_checked_before_health_and_probe_restored(monkeypatch):
    events = []

    class Probe:
        last = {"reason": "memory"}

        def __call__(self):
            events.append("health")
            return True

        def close(self):
            events.append("close")

    def original(**kwargs):
        return Probe()

    @contextmanager
    def lease():
        yield lambda: events.append("lease")

    monkeypatch.setattr(copier, "guard", lambda: None)
    monkeypatch.setattr(copier, "catalog_lease", lease)
    monkeypatch.setattr(copier.runner, "health_probe", original)

    def run():
        probe = copier.runner.health_probe()
        assert probe() is True and probe.last == {"reason": "memory"}
        probe.close()
        return {"status": "complete", "deleted": 0}

    monkeypatch.setattr(copier.runner, "run", run)
    assert copier.run()["deleted"] == 0
    assert events == ["lease", "health", "close"]
    assert copier.runner.health_probe is original


def test_lost_source_lease_prevents_health_or_transfer():
    class Probe:
        def __call__(self):
            pytest.fail("Lease is authoritative")

    def lost():
        raise SafetyPause("source_lease_lost")

    with pytest.raises(SafetyPause):
        copier.LeasedProbe(Probe(), lost)()
