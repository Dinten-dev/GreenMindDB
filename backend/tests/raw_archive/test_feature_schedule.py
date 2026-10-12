"""A busy copier yields briefly; RAM pauses must release the feature process."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


def schedule():
    path = Path(__file__).resolve().parents[3] / "deploy/archive-server/feature-backup-daily.py"
    spec = importlib.util.spec_from_file_location("feature_schedule", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_busy_copy_yield_window_is_retried_without_hour_gap(tmp_path, monkeypatch):
    module = schedule()
    results = iter(
        [
            SimpleNamespace(
                returncode=75, stdout=json.dumps({"status": "paused", "reason": "archive_job_busy"})
            ),
            SimpleNamespace(returncode=0, stdout=json.dumps({"status": "verified"})),
        ]
    )
    waits = []
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: next(results))
    monkeypatch.setattr(module.time, "sleep", waits.append)
    assert module.run_attempt(tmp_path) == 0
    assert waits == [20]
    assert (tmp_path / "try-0.json").exists() and (tmp_path / "try-1.json").exists()


def test_resource_pause_is_not_retried(tmp_path, monkeypatch):
    module = schedule()
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(
            returncode=75, stdout=json.dumps({"status": "paused", "reason": "memory"})
        ),
    )
    monkeypatch.setattr(
        module.time,
        "sleep",
        lambda _: (_ for _ in ()).throw(AssertionError("No waiting on RAM pause")),
    )
    assert module.run_attempt(tmp_path) == 75
    assert not (tmp_path / "try-1.json").exists()


def test_continuous_contention_is_finite(tmp_path, monkeypatch):
    module = schedule()
    calls, waits = [], []

    def busy(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(
            returncode=75, stdout=json.dumps({"status": "paused", "reason": "archive_job_busy"})
        )

    monkeypatch.setattr(module.subprocess, "run", busy)
    monkeypatch.setattr(module.time, "sleep", waits.append)
    assert module.run_attempt(tmp_path) == 75
    assert len(calls) == 4 and waits == [20, 20, 20]
