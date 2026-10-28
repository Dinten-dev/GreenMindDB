from datetime import UTC, datetime, timedelta

from app.raw_archive.observation import assessment

START = datetime(2026, 10, 3, tzinfo=UTC)


def samples():
    return [
        {
            "at": (START + timedelta(minutes=5 * i)).isoformat(),
            "protected_unchanged": True,
            "receiver_health": True,
            "headroom": True,
            "guard": {"reason": None},
            "source_progress": {"gateway": [i], "direct": [i]},
        }
        for i in range(288)
    ]


def test_elapsed_time_cannot_replace_real_progress():
    rows = samples()
    final = START + timedelta(hours=24)
    assert assessment(rows, START.isoformat(), final)["receiver_observation_passed"]
    for row in rows:
        row.pop("source_progress")
    result = assessment(rows, START.isoformat(), final)
    assert result["observation_complete"] and not result["receiver_observation_passed"]
    assert not result["deletion_authorized"]


def test_gaps_restarts_health_failures_and_early_checks_fail_closed():
    rows = samples()
    final = START + timedelta(hours=24)
    assert not assessment(rows, START.isoformat(), START + timedelta(hours=23))[
        "receiver_observation_passed"
    ]
    assert not assessment(rows[5:], START.isoformat(), final)["receiver_observation_passed"]
    rows[1]["protected_unchanged"] = False
    assert not assessment(rows, START.isoformat(), final)["receiver_observation_passed"]
    rows[1]["protected_unchanged"] = True
    rows[1]["receiver_health"] = False
    assert not assessment(rows, START.isoformat(), final)["receiver_observation_passed"]


def test_resource_pause_cannot_count_as_successful_24_hours():
    rows = samples()
    rows[10]["headroom"] = False
    rows[10]["guard"] = {"reason": "host_load"}
    result = assessment(rows, START.isoformat(), START + timedelta(hours=24))
    assert result["observation_complete"]
    assert not result["receiver_observation_passed"]
    assert result["headroom_failures"] == 1
    assert result["pauses"] == {"host_load": 1}


def test_missing_resource_measurement_blocks_observation_acceptance():
    rows = samples()
    del rows[10]["headroom"]
    rows[11]["guard"] = {}
    result = assessment(rows, START.isoformat(), START + timedelta(hours=24))
    assert not result["receiver_observation_passed"]
    assert result["headroom_failures"] == 2


def test_duplicate_samples_cannot_inflate_required_sample_count():
    rows = samples()
    rows.append(dict(rows[50]))
    result = assessment(rows, START.isoformat(), START + timedelta(hours=24))
    assert not result["receiver_observation_passed"]
    assert not result["sample_window_valid"]


def test_future_or_prebaseline_samples_cannot_support_acceptance():
    for timestamp in (START - timedelta(seconds=1), START + timedelta(hours=24, seconds=1)):
        rows = samples()
        rows.append(dict(rows[0], at=timestamp.isoformat()))
        result = assessment(rows, START.isoformat(), START + timedelta(hours=24))
        assert not result["receiver_observation_passed"]
        assert not result["sample_window_valid"]
