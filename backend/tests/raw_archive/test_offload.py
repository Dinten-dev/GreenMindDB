"""Daily offload, folder identity and verified deletion; no external data touched."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.raw_archive.daily import run_daily
from app.raw_archive.policy import ArchiveBlocked, Ledger, cutoff, sensor_folder


class Features:
    def __init__(self, catalog):
        self.catalog = catalog
        self.missing = False

    def resolve(self, reference):
        if self.missing:
            raise ArchiveBlocked("Current persisted Gateway features are not verified")
        return [
            replace(record, feature_digest="a" * 64) for record in self.catalog.resolve(reference)
        ]

    def revalidate(self, record):
        if self.missing:
            raise ArchiveBlocked("Features changed before deletion")
        self.catalog.revalidate(replace(record, feature_digest=""))


def offload(world, *, features_missing=False, corrupt_second_read=False):
    config, catalogs, source, destination, add, _ = world
    record = add("gateway", 1)
    now = cutoff(datetime.now(UTC)) + timedelta(days=2)
    features = {kind: Features(catalog) for kind, catalog in catalogs.items()}
    features["gateway"].missing = features_missing
    deletions = []

    def delete(record, snapshot):
        ledger = Ledger(config.root)
        try:
            state, receipt = ledger.load(record)
            assert state == "deleting"
            assert destination.data[receipt["remote_key"]] == source.data[record.key]
            assert len(destination.reads) >= 2
            assert snapshot["VersionId"] == record.sha256
        finally:
            ledger.close()
        deletions.append(record.key)
        source.data.pop(record.key)

    source.evict = delete
    original_read = destination.download

    def read(key, path, maximum):
        if corrupt_second_read and destination.reads:
            path.write_bytes(b"corrupt between copy and delete")
        else:
            original_read(key, path, maximum)

    destination.download = read
    result = run_daily(
        replace(config, delete_enabled=True, reads_accepted=True),
        catalogs,
        dict.fromkeys(catalogs, source),
        destination,
        healthy=lambda: True,
        source_identities=dict.fromkeys(catalogs, "test"),
        eviction_catalogs=features,
        now=now,
    )
    return result, record, deletions


def test_nightly_offload_deletes_only_after_second_full_verified_read(world):
    result, record, deletions = offload(world)
    assert result["status"] == "complete" and result["deleted"] == 1
    assert deletions == [record.key]
    assert record.key not in world[2].data
    path = next(iter(world[3].data))
    assert "/mac-14-c1-9f-d9-42-a4/" in path
    assert record.remote_key(world[0].namespace) == path


@pytest.mark.parametrize("failure", ["features", "corruption"])
def test_failed_predelete_check_retains_local_wav(world, failure):
    result, record, deletions = offload(
        world, features_missing=failure == "features", corrupt_second_read=failure == "corruption"
    )
    assert result["status"] == "incomplete" and result["failed"] == 1
    assert not deletions and record.key in world[2].data
    assert len(world[3].data) == 1  # The copy can exist while deletion remains blocked.


def test_day_folders_use_recording_start_in_switzerland(world):
    config, _, _, _, add, _ = world
    original = add("gateway", 1)
    record = replace(
        original,
        started_at=datetime(2026, 9, 24, 22, 30, tzinfo=UTC),
        ended_at=datetime(2026, 9, 24, 22, 31, tzinfo=UTC),
    )
    key = record.remote_key(config.namespace)
    assert key.startswith("production/2026-09-25/mac-14-c1-9f-d9-42-a4/003000000000_gateway_")
    different = replace(record, key="another.wav")
    assert different.remote_key(config.namespace) != key


def test_swiss_midnight_handles_dst_without_three_month_delay():
    assert cutoff(datetime(2026, 10, 25, 12, tzinfo=UTC)) == datetime(2026, 10, 24, 22, tzinfo=UTC)
    assert cutoff(datetime(2026, 10, 26, 12, tzinfo=UTC)) == datetime(2026, 10, 25, 23, tzinfo=UTC)


def test_sensor_folder_rejects_names_as_paths_and_keeps_stable_ids():
    assert sensor_folder("14:C1:9F:D9:42:A4", "gateway", "unused") == "mac-14-c1-9f-d9-42-a4"
    assert sensor_folder("14c19fd942a4", "direct", "unused") == "mac-14-c1-9f-d9-42-a4"
    assert sensor_folder(None, "direct", "00000000-0000-0000-0000-000000000001") == (
        "direct-00000000-0000-0000-0000-000000000001"
    )
    with pytest.raises(ValueError):
        sensor_folder("../../other", "direct", "../../other")


def test_approved_offload_can_relieve_a_full_source_volume(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from pathlib import Path
    from types import SimpleNamespace

    import httpx

    from app.raw_archive import health, runner

    monkeypatch.setenv(
        "RAW_ARCHIVE_HEALTH_URLS", "http://127.0.0.1:1/health,http://127.0.0.1:2/health"
    )
    monkeypatch.setenv("RAW_ARCHIVE_SOURCE_MOUNTS", str(tmp_path))
    original_read = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda path: "MemAvailable: 2097152 kB"
        if str(path) == "/proc/meminfo"
        else original_read(path),
    )
    monkeypatch.setattr(runner.os, "getloadavg", lambda: (0, 0, 0))
    monkeypatch.setattr(
        health.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(total=100 * 1024**3, used=99 * 1024**3, free=1024**3),
    )
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **_: nullcontext(
            SimpleNamespace(stream=lambda *args: nullcontext(SimpleNamespace(status_code=200)))
        ),
    )
    assert not runner.health_probe()()
    assert runner.health_probe(allow_low_source_space=True)()
