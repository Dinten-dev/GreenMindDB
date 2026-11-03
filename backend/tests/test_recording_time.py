"""Time provenance and coverage do not certify physical timing or invent missing samples."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services.recording_time import (
    anchor_available,
    recording_time,
    utc_filename,
    window_bounds,
    window_time,
)
from app.services.wav_service import generate_download_filename

START = datetime(2026, 10, 25, 0, 30, tzinfo=UTC)


def point(seconds, coverage=1, width=1):
    return {
        "timestamp": (START + timedelta(seconds=seconds)).isoformat(),
        "resolution_seconds": width,
        "coverage_ratio": coverage,
    }


def test_coverage_includes_missing_edges_and_partial_buckets():
    result = window_time([point(0), point(1, 0.5), point(3)], START, START + timedelta(seconds=4))
    assert result["coverage_ratio"] == 0.625
    assert result["gap_count"] == 1
    assert result["coverage_status"] == "incomplete"
    assert result["physical_accuracy_verified"] is False
    partial = window_time([point(1)], START, START + timedelta(seconds=3))
    assert partial["gap_count"] == 2 and partial["coverage_ratio"] == pytest.approx(1 / 3)


def test_unknown_coverage_and_overlap_are_never_claimed_complete():
    result = window_time([point(0, None)], START, START + timedelta(seconds=1))
    assert result["coverage_ratio"] is None and result["coverage_status"] == "unknown"
    result = window_time([point(0, width=2), point(1)], START, START + timedelta(seconds=2))
    assert result["overlap"] and result["coverage_ratio"] is None
    result = window_time(
        [{"timestamp": "2026-10-25T02:30:00", "value": 1}], START, START + timedelta(seconds=1)
    )
    assert result["anchor_status"] == "missing"
    assert result["recorded_from"] is None


def test_integer_anchor_sample_reference_hash_and_missing_anchor():
    start = 1792888200123456
    manifest = {
        "session_id": "synthetic",
        "config": {"session_start_us": start, "sample_rate": 400},
    }
    run = {
        "first_frame": 32,
        "frame_count": 400,
        "started_at_us": start + 80000,
        "sha256": "a" * 64,
    }
    info = recording_time(manifest, run)
    assert info["recorded_from"] == "2026-10-25T00:30:00.203456+00:00"
    assert info["recorded_to"] == "2026-10-25T00:30:01.203456+00:00"
    assert info["first_frame"] == 32 and info["sha256"] == "a" * 64
    assert info["physical_accuracy_verified"] is False
    manifest["config"].pop("session_start_us")
    unknown = recording_time(manifest, run)
    assert unknown["basis"] == "unknown" and unknown["anchor_utc"] is None
    assert not anchor_available({"session_start_us": start, "sample_rate": 0})


def test_dst_repeated_hour_and_fractional_export_bounds_remain_unique():
    zurich = ZoneInfo("Europe/Zurich")
    first = START.astimezone(zurich)
    second = (START + timedelta(hours=1)).astimezone(zurich)
    assert first.hour == second.hour == 2
    a = utc_filename("direct", "sensor", first)
    b = utc_filename("direct", "sensor", second)
    assert a != b and "T003000000000Z" in a and "T013000000000Z" in b
    name = generate_download_filename("Sensor", first, second, "zip")
    assert "T003000000000Z" in name and "T013000000000Z" in name
    assert "/" not in utc_filename("direct", "../../sensor", START)


def test_explicit_window_is_timezone_normalized_and_strict():
    start, end = window_bounds(
        86400, START.astimezone(ZoneInfo("Europe/Zurich")), START + timedelta(seconds=1)
    )
    assert start == START and start.tzinfo == UTC and end > start
    for left, right in [(START, None), (START, START), (START.replace(tzinfo=None), START)]:
        with pytest.raises(ValueError):
            window_bounds(86400, left, right)
