"""Execute isolated production preparation/rollout with Docker and SSH replaced."""

import json
import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "direct-production"
pytestmark = pytest.mark.skipif(not DEPLOY.is_dir(), reason="requires complete repository")


def test_prepare_requires_explicit_target(tmp_path, monkeypatch):
    script = tmp_path / "prepare.py"
    shutil.copy(DEPLOY / "prepare.py", script)
    monkeypatch.setattr(sys, "argv", [str(script)])
    with pytest.raises(SystemExit, match="Explicit preparation required"):
        runpy.run_path(str(script))
    assert not (tmp_path / "private").exists()


def test_prepare_is_separate_and_preserves_credentials_on_retry(tmp_path, monkeypatch):
    script = tmp_path / "prepare.py"
    shutil.copy(DEPLOY / "prepare.py", script)
    calls = []

    def execute(command, **kwargs):
        calls.append((command, kwargs.get("input", "")))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", execute)
    monkeypatch.setattr(sys, "argv", [str(script), "--prepare-production"])
    previous_umask = os.umask(0o077)
    try:
        runpy.run_path(str(script))
        first = (tmp_path / "private/credentials.json").read_bytes()
        runpy.run_path(str(script))
    finally:
        os.umask(previous_umask)
    assert (tmp_path / "private/credentials.json").read_bytes() == first
    env = dict(
        line.split("=", 1) for line in (tmp_path / "private/direct.env").read_text().splitlines()
    )
    assert env["DIRECT_ENVIRONMENT"] == "production"
    assert env["DIRECT_DASHBOARD_ORIGIN"] == "https://green-mind.ch"
    assert env["DIRECT_DASHBOARD_API_URL"] == "http://backend:8000/api/v1"
    assert env["DIRECT_S3_ENDPOINT_URL"] == "https://green-mind.ch:9444"
    assert env["DIRECT_S3_BUCKET"].startswith("greenmind-direct-production-")
    assert "@postgres:5432/greenmind_direct_production" in env["DIRECT_DATABASE_URL"]
    assert (tmp_path / "private/direct.env").stat().st_mode & 0o777 == 0o600
    assert {cmd[3] for cmd, _ in calls} == {"greenminddb-postgres-1", "greenminddb-minio-1"}
    text = "\n".join(data for _, data in calls)
    assert "staging" not in text
    assert "DROP " not in text
    policies = [json.loads(data) for _, data in calls if data.startswith('{"Version"')]
    for policy in policies:
        for statement in policy["Statement"]:
            assert all(
                "greenmind-direct-production-hotspot" in resource
                for resource in statement["Resource"]
            )


STUB = r"""
import json, os, sys
from pathlib import Path
name = Path(sys.argv[0]).name
args = sys.argv[1:]
command = " ".join(args)
with open(os.environ["CALL_LOG"], "a") as f:
    f.write(json.dumps({"tool": name, "args": args, "image": os.environ.get("DIRECT_BACKEND_IMAGE")}) + "\n")
if name == "docker":
    if " ps -q " in " " + command + " " and os.environ.get("EXISTING"):
        print("old-container")
    elif args[:1] == ["inspect"]:
        print("sha256:previous-image")
    elif "exec -T" in command and os.environ.get("FAIL_HEALTH"):
        sys.exit(1)
    elif "init-schema" in command and os.environ.get("FAIL_SCHEMA"):
        sys.exit(1)
"""


@pytest.fixture
def rollout(tmp_path):
    state = tmp_path / "state"
    (state / "private").mkdir(parents=True)
    (state / "private/direct.env").write_text("# test-only\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("docker", "flock", "sleep"):
        path = bin_dir / name
        path.write_text(f"#!{sys.executable}\n{STUB}")
        path.chmod(0o755)
    log = tmp_path / "calls.jsonl"

    def run(*args, **overrides):
        result = subprocess.run(
            ["/bin/bash", str(DEPLOY / "rollout.sh"), str(state), *args],
            env={
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "CALL_LOG": str(log),
                **overrides,
            },
            capture_output=True,
            text=True,
            timeout=30,
        )
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return result, calls

    return state, run


def test_disabled_rollout_never_calls_docker(rollout):
    state, run = rollout
    result, calls = run()
    assert result.returncode == 0
    assert calls == []
    assert not (state / "enabled").exists()


def test_activation_enables_only_after_schema_and_health(rollout):
    state, run = rollout
    result, calls = run("--activate")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (state / "enabled").is_file()
    commands = [" ".join(call["args"]) for call in calls if call["tool"] == "docker"]
    assert all("-p gm-direct-production" in command for command in commands)
    assert next(i for i, cmd in enumerate(commands) if "init-schema" in cmd) < next(
        i for i, cmd in enumerate(commands) if "up -d" in cmd
    )
    assert any(
        "hotspot_pairing" in command and 'health["assembler"]' in command for command in commands
    )


def test_failed_initial_activation_stops_only_direct_and_stays_disabled(rollout):
    state, run = rollout
    result, calls = run("--activate", FAIL_HEALTH="1")
    assert result.returncode != 0
    assert not (state / "enabled").exists()
    assert calls[-1]["args"][-1] == "stop"
    assert "gm-direct-production" in calls[-1]["args"]


def test_failed_update_restores_previous_direct_image(rollout):
    state, run = rollout
    (state / "enabled").touch()
    (state / "compose.yml").write_text("# previous compose\n")
    result, calls = run(EXISTING="1", FAIL_HEALTH="1")
    assert result.returncode != 0
    assert (state / "compose.yml").read_text() == "# previous compose\n"
    assert calls[-1]["image"] == "greenmind-direct-production:rollback"
    assert calls[-1]["args"][-3:] == ["up", "-d", "--no-build"]


def test_schema_failure_never_restarts_running_services(rollout):
    state, run = rollout
    (state / "enabled").touch()
    result, calls = run(FAIL_SCHEMA="1")
    assert result.returncode != 0
    assert not any("up" in call["args"] or "stop" in call["args"] for call in calls)
