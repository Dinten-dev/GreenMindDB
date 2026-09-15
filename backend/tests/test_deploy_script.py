"""Exercise the real deploy script without any network or Docker side effects."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "deploy.sh"
STUB = r"""
import json
import os
from pathlib import Path
import sys

tool = Path(sys.argv[0]).name
args = sys.argv[1:]
command = " ".join(args)
entry = {"tool": tool, "args": args}
if tool == "ssh" and command.endswith("docker image load"):
    entry["stream"] = sys.stdin.read()
with open(os.environ["CALL_LOG"], "a") as log:
    log.write(json.dumps(entry) + "\n")
failure = os.environ.get("FAIL_COMMAND")
if failure and failure in command:
    sys.exit(7)
if tool == "ssh" and args[-2:] == ["uname", "-m"]:
    print(os.environ.get("REMOTE_ARCH", "x86_64"))
elif tool == "ssh" and "curl -sf" in command:
    print("yes")
elif tool == "docker" and args[:2] == ["image", "save"]:
    print("finished-image-archive")
"""


@pytest.fixture
def deploy(tmp_path):
    if not SCRIPT.is_file():
        pytest.skip("Deployment tests require the repository checkout, not the backend-only image")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("ssh", "docker", "rsync"):
        executable = bin_dir / name
        executable.write_text(f"#!{sys.executable}\n{STUB}")
        executable.chmod(0o755)
    key = tmp_path / "fake-key"
    key.write_text("FAKE PRIVATE KEY HEADER FOR LOCAL TEST ONLY\n")
    known_hosts = tmp_path / "known-hosts"
    known_hosts.touch()
    call_log = tmp_path / "calls.jsonl"

    def run(environment="staging", *options, **overrides):
        env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "DEPLOY_USER": "test-user",
            "DEPLOY_HOST": "example.invalid",
            "DEPLOY_SSH_KEY_FILE": str(key),
            "DEPLOY_KNOWN_HOSTS_FILE": str(known_hosts),
            "DEPLOY_REMOTE_BASE_DIR": "/home/test-user",
            "CALL_LOG": str(call_log),
            "FAIL_COMMAND": "",
            "REMOTE_ARCH": "x86_64",
            **overrides,
        }
        result = subprocess.run(
            ["/bin/bash", str(SCRIPT), "--env", environment, *options],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        calls = [json.loads(line) for line in call_log.read_text().splitlines()]
        return result, calls

    return run


@pytest.mark.parametrize(
    ("architecture", "platform"),
    [("x86_64", "linux/amd64"), ("aarch64", "linux/arm64")],
)
def test_staging_builds_on_caller_and_transfers_before_start(deploy, architecture, platform):
    result, calls = deploy(REMOTE_ARCH=architecture)
    assert result.returncode == 0, result.stdout + result.stderr
    builds = [call for call in calls if call["tool"] == "docker" and call["args"][0] == "build"]
    assert len(builds) == 2
    for build, service in zip(builds, ("backend", "frontend"), strict=True):
        assert build["args"] == [
            "build",
            "--platform",
            platform,
            "-t",
            f"greenmind-{service}-staging:latest",
            str(SCRIPT.parents[1] / service),
        ]
    load_index = next(index for index, call in enumerate(calls) if "stream" in call)
    assert calls[load_index]["stream"] == "finished-image-archive\n"
    start_index = next(
        index for index, call in enumerate(calls) if " up -d " in " ".join(call["args"])
    )
    assert load_index < start_index
    start = " ".join(calls[start_index]["args"])
    assert "COMPOSE_PROJECT_NAME=gm-staging" in start
    assert "docker-compose.staging.yml up -d --no-build" in start
    assert all(
        ".yml build" not in " ".join(call["args"]) for call in calls if call["tool"] == "ssh"
    )


@pytest.mark.parametrize(
    "failure",
    ["-t greenmind-backend", "-t greenmind-frontend", "image save", "image load"],
)
def test_staging_does_not_restart_services_after_failed_build_or_transfer(deploy, failure):
    result, calls = deploy(FAIL_COMMAND=failure)
    assert result.returncode != 0
    assert not any(" up -d " in " ".join(call["args"]) for call in calls)


def test_staging_rejects_unknown_architecture_before_build_or_restart(deploy):
    result, calls = deploy(REMOTE_ARCH="unsupported")
    assert result.returncode != 0
    assert not any(call["tool"] == "docker" for call in calls)
    assert not any(" up -d " in " ".join(call["args"]) for call in calls)


def test_staging_skip_build_never_falls_back_to_remote_build(deploy):
    result, calls = deploy("staging", "--skip-build")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not any(call["tool"] == "docker" for call in calls)
    assert not any("stream" in call for call in calls)
    assert any(
        "docker-compose.staging.yml up -d --no-build" in " ".join(call["args"]) for call in calls
    )


@pytest.mark.parametrize("skip", [False, True])
def test_production_deploy_behavior_is_preserved(deploy, skip):
    result, calls = deploy("production", *(["--skip-build"] if skip else []))
    assert result.returncode == 0, result.stdout + result.stderr
    assert not any(call["tool"] == "docker" or "stream" in call for call in calls)
    start = next(" ".join(call["args"]) for call in calls if " up -d " in " ".join(call["args"]))
    assert "COMPOSE_PROJECT_NAME=greenminddb" in start
    assert "docker-compose.prod.yml up -d --remove-orphans" in start
    assert ("docker-compose.prod.yml build &&" in start) is not skip


def test_only_production_checks_explicit_direct_activation(deploy):
    result, calls = deploy("production")
    assert result.returncode == 0
    assert any(
        "deploy/direct-production/rollout.sh /home/test-user/greenmind-direct-production"
        in " ".join(call["args"])
        for call in calls
    )


def test_staging_never_activates_production_direct(deploy):
    result, calls = deploy("staging")
    assert result.returncode == 0
    assert not any("rollout.sh" in " ".join(call["args"]) for call in calls)
