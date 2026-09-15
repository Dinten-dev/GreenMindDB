import math
from datetime import UTC, datetime, timedelta

from app.visualization.core import bucket, merge_readings, pack_ids, unpack_ids
from app.visualization.storage import read_archive, write_archive


def test_exact_timestamp_deduplication_and_weighted_statistics():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    points = [
        {"timestamp": start + timedelta(seconds=i), "value": value}
        for i, value in enumerate([1, 2, 9])
    ]
    first = merge_readings(None, points[:2])
    # A retried identity cannot change a value, matching the Legacy primary key.
    combined = merge_readings(first, points + [{"timestamp": start, "value": 999}])
    assert combined["n"] == 3
    assert combined["total"] == 12
    assert combined["total2"] == 86
    assert combined["minimum"] == 1
    assert combined["maximum"] == 9
    assert math.sqrt(combined["total2"] / combined["n"]) > combined["total"] / combined["n"]
    assert len(unpack_ids(combined["identities"])) == 3


def test_timestamp_microseconds_survive_identity_ledger():
    values = {123456789000001, 123456789000002, 123456789999999}
    assert unpack_ids(pack_ids(values)) == values
    assert bucket(datetime(2026, 1, 1, 12, 39, 59, tzinfo=UTC), 600).minute == 30


def test_verified_archive_is_replayable_without_overwriting(tmp_path):
    rows = [
        {"timestamp": "2026-01-01T00:00:00+00:00", "value": 1.25, "unit": "mV"},
        {"value": None},
    ]
    first = write_archive(tmp_path, iter(rows), "test")
    second = write_archive(tmp_path, iter(rows), "test")
    assert first["path"] != second["path"]
    assert first["sha256"] == second["sha256"]
    assert list(read_archive(first)) == rows
