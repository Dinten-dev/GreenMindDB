"""Exact production pilots cannot discover sources or exceed global WAV limits."""

import copy
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.raw_archive.health import SafetyPause
from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import runtime, schedule
from tests.test_tiered_wav_copy import REFERENCE, fixture

AT = datetime(2026, 10, 31, tzinfo=UTC)
GATEWAY = "00000000-0000-0000-0000-000000000004"


def plan(jobs=None, *, wavs=10, size=5 * 1024**2):
    return {
        "schema": 1,
        "environment": "production",
        "wav_jobs": jobs or [{"source": "direct", "reference": REFERENCE}],
        "wav_limits": {"max_wavs": wavs, "max_bytes": size},
    }


@pytest.fixture
def pilot(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule, "environment", lambda: "production")
    monkeypatch.setattr(schedule, "state_root", lambda: tmp_path)
    monkeypatch.setattr(schedule, "Ledger", lambda *_: pytest.fail("No sweep/bundle scan"))
    for name in (
        "wav_source",
        "publish_wav",
        "publish_sql",
        "backup",
        "latest_snapshot",
        "verify",
        "evict",
    ):
        monkeypatch.setattr(
            runtime, name, lambda *a, **k: pytest.fail("No scans or other operations")
        )
    closed = []

    class Healthy:
        def __call__(self):
            return True

        def close(self):
            closed.append(True)

    monkeypatch.setattr(schedule, "health_probe", lambda *_: Healthy())
    path = tmp_path / "exact.json"

    def tick(value, **kwargs):
        path.write_text(json.dumps(value))
        path.chmod(0o600)
        return schedule.tick(path, clock=lambda: AT, **kwargs)

    return SimpleNamespace(tick=tick, closed=closed)


@pytest.mark.parametrize(
    "extra", ["wav_sources", "scopes", "backups", "snapshot_id", "scheduled_snapshots"]
)
def test_mixed_or_scanning_plans_fail_before_state_or_source(pilot, monkeypatch, extra):
    value = plan()
    value[extra] = []
    monkeypatch.setattr(
        schedule, "health_probe", lambda *_: pytest.fail("No health/state initialization")
    )
    monkeypatch.setattr(runtime, "stage_wav", lambda *_a, **_k: pytest.fail("No transfer"))
    with pytest.raises(ArchiveBlocked, match="cannot mix"):
        pilot.tick(value)


@pytest.mark.parametrize(
    "job",
    [
        {"source": "direct", "reference": [REFERENCE[0]]},
        {"source": "direct", "reference": [REFERENCE[0], True]},
        {"source": "direct", "reference": [REFERENCE[0], 0]},
        {"source": "direct", "reference": [REFERENCE[0], 2**31]},
        {"source": "gateway", "reference": [REFERENCE[0], 1]},
        {"source": "gateway", "reference": "some-string"},
        {"source": "gateway", "reference": ["not-a-uuid"]},
        {"source": "gateway", "reference": ["00000000-0000-0000-0000-00000000000A"]},
        {"source": "unknown", "reference": [REFERENCE[0]]},
        {"source": [], "reference": [REFERENCE[0]]},
        {"source": "direct", "reference": REFERENCE, "other": 1},
    ],
)
def test_invalid_exact_references_fail_closed(job):
    with pytest.raises(ArchiveBlocked):
        schedule.exact_wav_jobs(plan([job]))


@pytest.mark.parametrize(
    "wavs,size", [(True, 1000), (11, 1000), (0, 1000), (1, True), (1, 0), (1, 5 * 1024**2 + 1)]
)
def test_global_pilot_limits_cannot_be_widened(wavs, size):
    with pytest.raises(ArchiveBlocked):
        schedule.exact_wav_jobs(plan(wavs=wavs, size=size))


def test_duplicate_jobs_and_bool_schema_fail():
    value = plan()
    value["wav_jobs"] *= 2
    with pytest.raises(ArchiveBlocked, match="Duplicate"):
        schedule.exact_wav_jobs(value)
    value = plan()
    value["schema"] = True
    with pytest.raises(ArchiveBlocked, match="schema"):
        schedule.exact_wav_jobs(value)


def test_global_bytes_and_file_caps_are_shared_across_jobs(pilot, monkeypatch):
    calls = []

    def stage(source, reference, **limits):
        calls.append((source, reference, limits))
        assert limits["can_continue"]()
        count, size = (1, 804) if len(calls) == 1 else (1, 456044)
        assert count <= limits["maximum_wavs"] and size <= limits["maximum_bytes"]
        return {"complete": True, "copied_wavs": count, "copied_bytes": size}

    monkeypatch.setattr(runtime, "stage_wav", stage)
    jobs = [
        {"source": "gateway", "reference": [GATEWAY]},
        {"source": "direct", "reference": REFERENCE},
    ]
    result = pilot.tick(plan(jobs, size=456848))
    assert result["copied_wavs"] == 2 and result["copied_wav_bytes"] == 456848
    assert calls[1][2]["maximum_bytes"] == 456044 and calls[1][2]["maximum_wavs"] == 9
    assert calls[1][2]["maximum_object_bytes"] == 456848
    assert result["evicted"] == result["verified"] == 0 and pilot.closed == [True]
    calls.clear()
    result = pilot.tick(plan(jobs, wavs=1, size=456848))
    assert len(calls) == 1 and result["copied_wavs"] == 1 and result["deferred"] == 1


def test_real_advance_resumes_selected_revision_under_shared_invocation_budget(
    pilot, monkeypatch, tmp_path
):
    h = fixture(tmp_path / "actual", 12)

    def stage(source, reference, **limits):
        assert source == "direct" and reference == REFERENCE
        return h.advance(**limits)

    monkeypatch.setattr(runtime, "stage_wav", stage)
    try:
        value = plan(size=5 * 804)
        first = pilot.tick(value)
        assert first["copied_wavs"] == 5 and first["copied_wav_bytes"] == 4020
        assert first["published"] == 0 and first["pending_wavs"] == 1
        second = pilot.tick(value)
        third = pilot.tick(value)
        assert second["copied_wavs"] == 5 and third["copied_wavs"] == 2
        assert third["published"] == 1 and third["pending_wavs"] == 0
        assert len([key for key in h.store.puts if key.endswith(".wav")]) == 12
        assert pilot.tick(value)["copied_wavs"] == 0
    finally:
        h.ledger.close()


def test_oversize_single_object_is_blocked_before_HEAD_GET_or_publish(pilot, monkeypatch, tmp_path):
    h = fixture(tmp_path / "oversize", 1)
    h.adapter.objects.snapshot = lambda *_: pytest.fail("No object access")
    h.adapter.objects.download = lambda *_: pytest.fail("No original GET")
    h.store.publish = lambda *_: pytest.fail("No Box upload")
    monkeypatch.setattr(
        runtime, "stage_wav", lambda _source, _reference, **limits: h.advance(**limits)
    )
    try:
        with pytest.raises(ArchiveBlocked, match="One WAV exceeds"):
            pilot.tick(plan(size=803))
    finally:
        h.ledger.close()


def test_fresh_source_does_not_bypass_existing_closed_day_boundary(pilot, monkeypatch, tmp_path):
    h = fixture(tmp_path / "fresh", 1)
    original = h.adapter.metadata

    def metadata(reference):
        envelope, headers, flags = original(reference)
        envelope = copy.deepcopy(envelope)
        envelope["body"]["originals"][0]["recording"]["received_at"] = AT.isoformat()
        return envelope, headers, flags

    h.adapter.metadata = metadata
    h.adapter.objects.snapshot = lambda *_: pytest.fail("No object access")
    monkeypatch.setattr(
        runtime, "stage_wav", lambda _source, _reference, **limits: h.advance(**limits)
    )
    try:
        with pytest.raises(ArchiveBlocked, match="closed-day"):
            pilot.tick(plan())
    finally:
        h.ledger.close()


def test_partial_transfer_failure_never_reallocates_unknown_budget(pilot, monkeypatch):
    calls = []

    def stage(*_args, **_kwargs):
        calls.append(True)
        raise ArchiveBlocked("synthetic error after one verified PUT")

    monkeypatch.setattr(runtime, "stage_wav", stage)
    jobs = [
        {"source": "gateway", "reference": [GATEWAY]},
        {"source": "direct", "reference": REFERENCE},
    ]
    with pytest.raises(ArchiveBlocked):
        pilot.tick(plan(jobs))
    assert calls == [True] and pilot.closed == [True]


def test_source_lease_loss_stops_exact_job_and_closes_probe(pilot, monkeypatch):
    calls = [0]

    def check():
        calls[0] += 1
        if calls[0] >= 3:
            raise SafetyPause("source_lease_lost")

    monkeypatch.setattr(schedule, "source_checkpoint", check)
    monkeypatch.setattr(runtime, "stage_wav", lambda *_a, **_k: pytest.fail("No copy"))
    with pytest.raises(SafetyPause, match="source_lease_lost"):
        pilot.tick(plan())
    assert pilot.closed == [True]


def test_exact_contract_rejects_unknown_environment():
    value = plan()
    value["environment"] = "other"
    with pytest.raises(ArchiveBlocked, match="environment"):
        schedule.exact_wav_jobs(value)
