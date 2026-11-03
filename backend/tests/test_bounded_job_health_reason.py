import json
from contextlib import nullcontext

import pytest

from app.raw_archive.health import SafetyPause
from app.tiered_archive import execution


@pytest.mark.parametrize("reason", ["source_load", "source_probe_stale", "receiver_health"])
def test_health_failure_survives_durable_outcome_without_details(tmp_path, monkeypatch, reason):
    monkeypatch.setattr(execution, "monitored", lambda *_args, **_kwargs: nullcontext())
    with pytest.raises(SafetyPause):
        with execution.bounded_job(tmp_path, seconds=3, emit=False):
            raise SafetyPause(
                "receiver_or_archive_resources",
                {
                    "reason": reason,
                    "url": "SECRET_URL",
                    "token": "SECRET_TOKEN",
                },
            )
    raw = (tmp_path / "copy-last-run.json").read_text()
    value = json.loads(raw)
    assert value["state"] == "paused"
    assert value["reason"] == "receiver_or_archive_resources"
    assert value["health_reason"] == reason
    assert "SECRET" not in raw


@pytest.mark.parametrize("reason", ["SECRET_REASON", ["source_load"], None])
def test_untrusted_health_details_are_not_persisted(tmp_path, monkeypatch, reason):
    monkeypatch.setattr(execution, "monitored", lambda *_args, **_kwargs: nullcontext())
    with pytest.raises(SafetyPause):
        with execution.bounded_job(tmp_path, seconds=3, emit=False):
            raise SafetyPause("receiver_or_archive_resources", {"reason": reason})
    raw = (tmp_path / "copy-last-run.json").read_text()
    assert "health_reason" not in json.loads(raw)
    assert "SECRET_REASON" not in raw
