"""Bounded offload validation cannot turn resource pauses into full recovery."""

import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


def module():
    path = Path(__file__).resolve().parents[3] / "deploy/archive-server/validate-archive-catalog.py"
    spec = importlib.util.spec_from_file_location("archive_validation", path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def configured(tmp_path, monkeypatch):
    script = module()
    monkeypatch.setattr(script, "STATE", tmp_path)
    monkeypatch.setattr(script.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=os.geteuid()))
    output = tmp_path / "new"
    output.mkdir(mode=0o700)
    return script, output


def response(status, reason=None, code=0):
    value = {"status": status, "deleted_files": 0}
    if reason:
        value["reason"] = reason
    return SimpleNamespace(returncode=code, stdout=json.dumps(value))


def test_busy_retry_completes_stages_but_not_catalog_or_restore(tmp_path, monkeypatch):
    script, output = configured(tmp_path, monkeypatch)
    answers = iter(
        [
            response("paused", "archive_job_busy", 75),
            response("complete"),
            response("complete"),
        ]
    )
    waits = []
    monkeypatch.setattr(script.subprocess, "run", lambda *a, **kw: next(answers))
    monkeypatch.setattr(script.time, "sleep", waits.append)
    assert script.worker(output) == 0 and waits == [20]
    proof = json.loads((output / "validation.json").read_text())
    assert proof["status"] == "metadata_stages_complete"
    assert proof["complete_catalog"] is False and proof["independent_restoration"] is False
    assert set(proof["results"]) == {"gateway", "direct"}


@pytest.mark.parametrize("reason", ["source_load", "source_memory"])
def test_resource_pause_has_no_retry_or_direct_query(tmp_path, monkeypatch, reason):
    script, output = configured(tmp_path, monkeypatch)
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        return response("paused", reason, 75)

    monkeypatch.setattr(script.subprocess, "run", run)
    monkeypatch.setattr(script.time, "sleep", lambda _: pytest.fail("No resource retry"))
    assert script.worker(output) == 75 and len(calls) == 1
    assert not (output / "direct-try-0.json").exists()


def test_busy_wait_is_finite(tmp_path, monkeypatch):
    script, output = configured(tmp_path, monkeypatch)
    calls, waits = [], []

    def run(*args, **kwargs):
        calls.append(args)
        return response("paused", "archive_job_busy", 75)

    monkeypatch.setattr(script.subprocess, "run", run)
    monkeypatch.setattr(script.time, "sleep", waits.append)
    assert script.worker(output) == 75
    assert len(calls) == 4 and waits == [20, 20, 20]


def test_failed_child_cannot_supply_completion_proof(tmp_path, monkeypatch):
    script, output = configured(tmp_path, monkeypatch)
    monkeypatch.setattr(script.subprocess, "run", lambda *a, **kw: response("complete", code=1))
    with pytest.raises(RuntimeError, match="cannot prove"):
        script.worker(output)
    assert not (output / "validation.json").exists()
