"""Read-only acceptance preflight must never invoke server operations."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/release/release.py"


@pytest.fixture
def candidate(tmp_path):
    manifest = {
        "revision": "a" * 40,
        "backend_image": "sha256:" + "b" * 64,
        "frontend_image": "sha256:" + "c" * 64,
    }
    evidence = manifest | {
        "environment": "staging",
        "checked_at": time.time(),
        "gateway_direct_parallel": True,
        "wav_sample_integrity": True,
        "interruption_recovery": True,
        "rollback_rehearsal": True,
        "ui_review": True,
    }
    report = tmp_path / "acceptance.json"
    report.write_text(json.dumps(evidence))
    command = [sys.executable, str(SCRIPT), "check-acceptance", "--acceptance", str(report)]
    for key, value in manifest.items():
        command.extend(["--" + key.replace("_", "-"), value])
    return manifest, evidence, report, command


def preflight(command, tmp_path):
    return subprocess.run(
        command,
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": str(tmp_path / "no-external-tools"),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_read_only_preflight_checks_exact_candidate_without_authorizing(candidate, tmp_path):
    manifest, _, report, command = candidate
    before = report.read_bytes()
    result = preflight(command, tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "acceptance_valid": True,
        "release_authorized": False,
        **manifest,
    }
    assert report.read_bytes() == before
    assert list(tmp_path.iterdir()) == [report]


@pytest.mark.parametrize(
    "patch",
    [
        {"revision": "d" * 40},
        {"backend_image": "sha256:" + "e" * 64},
        {"frontend_image": "sha256:" + "f" * 64},
        {"environment": "production"},
        {"checked_at": time.time() - 8 * 86400},
        {"checked_at": time.time() + 3600},
        {"checked_at": None},
        {"gateway_direct_parallel": False},
        {"wav_sample_integrity": False},
        {"interruption_recovery": False},
        {"rollback_rehearsal": False},
        {"ui_review": "true"},
    ],
)
def test_incomplete_acceptance_cannot_pass(candidate, tmp_path, patch):
    _, evidence, report, command = candidate
    report.write_text(json.dumps(evidence | patch))
    result = preflight(command, tmp_path)
    assert result.returncode == 1
    assert result.stdout == ""
    assert "no release authorized" in result.stderr
    assert str(report) not in result.stderr


@pytest.mark.parametrize("contents", ["not JSON", "[]", "null"])
def test_malformed_evidence_is_rejected(candidate, tmp_path, contents):
    _, _, report, command = candidate
    report.write_text(contents)
    assert preflight(command, tmp_path).returncode == 1


def test_missing_evidence_and_missing_arguments_are_rejected(candidate, tmp_path):
    _, _, report, command = candidate
    report.unlink()
    assert preflight(command, tmp_path).returncode == 1
    assert preflight(command[:-2], tmp_path).returncode == 2
    assert preflight([sys.executable, str(SCRIPT), "start"], tmp_path).returncode == 2
