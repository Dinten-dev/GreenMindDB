from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.visualization.feature_series import series


def test_verified_summary_preserves_actual_boundaries_and_sample_statistics():
    start = datetime(2026, 9, 24, tzinfo=UTC)
    row = dict(
        first=start,
        last=start + timedelta(minutes=21),
        n=228000,
        mean=100,
        square_mean=10025,
        minimum=90,
        maximum=120,
        duration=1200,
        recordings=2,
        overlap_seconds=0,
    )
    calls = []

    def execute(sql, params):
        calls.append(str(sql))
        return (
            SimpleNamespace(all=lambda: [("bio_signal", "mV")])
            if len(calls) == 1
            else SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: [row]))
        )

    result = series(
        SimpleNamespace(execute=execute), "sensor", start, start + timedelta(days=7), 600, 1205
    )[0]["data"][0]
    assert result["value"] == 100
    assert result["standard_deviation"] == 5
    assert result["resolution_seconds"] == 1260
    assert result["source_interval_end"] == row["last"].isoformat()
    assert result["coverage_ratio"] == 1200 / 1260
    assert result["reading_count"] == 228000
    assert "f.source_sha256=w.content_sha256" in calls[1]
    assert "AS overlap_seconds" in calls[1]


def test_mixed_environmental_sensor_keeps_existing_reading_path():
    db = SimpleNamespace(
        execute=lambda *_: SimpleNamespace(all=lambda: [("temperature", "C"), ("bio_signal", "mV")])
    )
    assert series(db, "sensor", None, None, 600, 1205) is None


def test_invalid_summary_is_not_visualized():
    start = datetime.now(UTC)
    row = dict(
        first=start,
        last=start,
        n=1,
        mean=1,
        square_mean=1,
        minimum=1,
        maximum=1,
        duration=0,
        recordings=1,
        overlap_seconds=0,
    )
    responses = iter(
        [
            SimpleNamespace(all=lambda: [("bioelectric", "mV")]),
            SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: [row])),
        ]
    )
    db = SimpleNamespace(execute=lambda *_: next(responses))
    with pytest.raises(ValueError):
        series(db, "sensor", start, start, 600, 1205)
