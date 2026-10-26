"""Actual fixed host adapter fails closed before source writers resume."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "deploy/archive-server/staging-cutover-control.py"
spec = importlib.util.spec_from_file_location("staging_control", SCRIPT)
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


@pytest.mark.parametrize("cohort", ["../escape", "f" * 25, "F" * 24, "f" * 23, "a/../" + "f" * 19])
def test_cohort_rejects_traversal(cohort, tmp_path, monkeypatch):
    monkeypatch.setattr(control, "ROOT", tmp_path)
    with pytest.raises(AssertionError):
        control.cohort_path(cohort)


def test_unknown_target_prevents_source_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(control, "fence_target", lambda _: {"stopped": False, "accepted": False})
    monkeypatch.setattr(control, "run", lambda *_: pytest.fail("Cannot resume unknown target"))
    with pytest.raises(AssertionError):
        control.rollback_source(tmp_path, "f" * 24)


def test_accepted_owner_withholds_rollback(tmp_path, monkeypatch):
    (tmp_path / "accepted.json").write_text("{}")
    monkeypatch.setattr(control, "fence_target", lambda _: pytest.fail("Preserve accepted owner"))
    assert control.rollback_source(tmp_path, "f" * 24)["rollback_withheld"]


def test_lost_accept_ack_preserves_target(tmp_path, monkeypatch):
    monkeypatch.setattr(control, "fence_target", lambda _: {"stopped": False, "accepted": True})
    monkeypatch.setattr(control, "run", lambda *_: pytest.fail("Cannot create duplicate writer"))
    assert control.rollback_source(tmp_path, "f" * 24)["reason"] == "accepted_target_owner"


def test_confirmed_stop_precedes_source_resume(tmp_path, monkeypatch):
    events = []
    (tmp_path / "baseline.json").write_text(
        json.dumps({"pause_containers": ["staging-source"], "receivers": []})
    )
    monkeypatch.setattr(
        control,
        "fence_target",
        lambda _: events.append("fenced") or {"stopped": True, "accepted": False},
    )
    monkeypatch.setattr(control, "proxy", lambda *_: events.append("restored_proxy"))
    monkeypatch.setattr(
        control,
        "run",
        lambda args, *_: events.append(
            "watchdog_stopped" if args[0] == "systemctl" else "source_start"
        ),
    )
    monkeypatch.setattr(control, "inspect", lambda _: [{"running": True}])
    monkeypatch.setattr(control, "health", lambda _: events.append("receiver_check"))
    assert control.rollback_source(tmp_path, "f" * 24)["rollback_verified"]
    assert events == [
        "fenced",
        "restored_proxy",
        "source_start",
        "receiver_check",
        "watchdog_stopped",
    ]


def test_stale_cohort_cannot_stop_new_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(
        control, "target_config", lambda _: {"services": {"app": {"container_name": "staging-app"}}}
    )
    monkeypatch.setattr(control, "inspect", lambda _: [{"name": "/staging-app", "running": True}])

    def run(args, *_):
        assert args[:2] == ["docker", "inspect"], "Cannot stop newer owner"
        return b"/different-cohort/compose.json\n"

    monkeypatch.setattr(control, "run", run)
    with pytest.raises(AssertionError, match="Different Staging owner"):
        control.target_stop(tmp_path)
