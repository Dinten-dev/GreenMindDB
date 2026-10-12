"""Paused work stays distinct from completion and real data failures."""

import json

import pytest

from app.raw_archive import runner
from app.raw_archive.health import SafetyPause


@pytest.mark.parametrize(
    "report,code",
    [
        ({"status": "complete"}, 0),
        ({"status": "disabled"}, 0),
        ({"status": "incomplete", "pending": 10, "failed": 0, "errors": []}, 75),
        ({"status": "blocked", "pause_code": "memory"}, 75),
        ({"status": "incomplete", "failed": 1, "errors": [{}]}, 1),
        ({"status": "blocked", "error": "InvalidMetadata"}, 1),
    ],
)
def test_exit_code_preserves_incomplete_evidence(monkeypatch, capsys, report, code):
    monkeypatch.setattr(runner, "run", lambda _metrics: dict(report))
    assert runner.main() == code
    assert json.loads(capsys.readouterr().out)["status"] == report["status"]


def test_early_resource_pause_returns_retry_status(monkeypatch, capsys):
    def pause(_metrics):
        raise SafetyPause("memory", {"required_mib": 512})

    monkeypatch.setattr(runner, "run", pause)
    assert runner.main() == 75
    assert json.loads(capsys.readouterr().out)["pause_code"] == "memory"
