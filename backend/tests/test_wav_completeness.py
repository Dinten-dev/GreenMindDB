from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.services.wav_completeness import direct_completeness, gateway_completeness


def recording(seconds, start_seconds=0):
    return SimpleNamespace(
        sample_rate=380,
        duration_seconds=seconds,
        started_at=datetime(2026, 10, 10, 12, 0, start_seconds, tzinfo=UTC),
    )


def test_peters_file_is_not_certified_by_legacy_coverage():
    result = gateway_completeness(recording(457))
    assert result["received_samples"] == 173660
    assert result["expected_samples"] == 228000
    assert result["coverage_ratio"] == pytest.approx(457 / 600)
    assert result["missing_seconds"] == 143
    assert result["status"] == "short"
    assert result["close_reason"] == "unknown"


def test_complete_and_initial_partial_are_distinct():
    assert gateway_completeness(recording(600))["status"] == "complete"
    assert gateway_completeness(recording(570, 30))["status"] == "partial_start"
    assert gateway_completeness(recording(500, 30))["status"] == "short"


def test_manifest_gap_and_collecting_status():
    manifest = {
        "config": {"sample_rate": 380},
        "first_frame": 0,
        "end_frame": 228000,
        "runs": [{"frame_count": 173660}],
        "missing_frame_ranges": [[173660, 228000]],
    }
    assert direct_completeness(manifest, False)["status"] == "collecting"
    result = direct_completeness(manifest, True)
    assert result["status"] == "partial"
    assert result["missing_seconds"] == 143
    assert result["missing_frame_ranges"] == [[173660, 228000]]
