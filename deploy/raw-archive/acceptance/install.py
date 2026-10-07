"""Install only archive-job coordination; existing running jobs finish normally."""

import hashlib
import json
import os
import pwd
import shutil
import subprocess
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[3]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def protected():
    names = ["gm-zones-production-2e687bec010a-application-1", "gm-direct-production-direct-api-1", "greenminddb-backend-1"]
    rows = subprocess.check_output(["docker", "inspect", "--format", "{{.Name}} {{.State.StartedAt}} {{.RestartCount}}", *names], text=True, timeout=8)
    for port in (8120, 8003, 8000):
        subprocess.run(["curl", "--max-time", "3", "-fsS", "-o", "/dev/null", f"http://127.0.0.1:{port}/health"], check=True, timeout=4)
    return {"receivers": rows, "production_proxy": digest(Path("/etc/nginx/sites-available/greenmind-prod")), "staging_proxy": digest(Path("/etc/nginx/sites-available/greenmind-staging"))}


def main():
    assert os.geteuid() == 0
    bundle = json.loads((PACKAGE / "bundle.json").read_text())
    revision = bundle["revision"]
    assert len(revision) == 40 and all(c in "0123456789abcdef" for c in revision)
    for name, expected in bundle["files"].items():
        path = PACKAGE / name
        assert not path.is_symlink() and PACKAGE in path.resolve().parents and digest(path) == expected
    before = protected()
    assert before["production_proxy"] == bundle["proxy_before_sha256"]
    runtime = Path("/opt/greenmind/archive-acceptance") / revision[:12]
    assert not runtime.exists()
    runtime.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PACKAGE, runtime)
    for path in runtime.rglob("*"):
        assert not path.is_symlink()
        os.chown(path, 0, 0)
        path.chmod(0o755 if path.is_dir() else 0o644)
    runtime.chmod(0o755)
    account = pwd.getpwnam("greenmind-raw-copy")
    coordinate = Path("/var/lib/greenmind-archive-coordination")
    assert not coordinate.exists()
    coordinate.mkdir(mode=0o2770)
    os.chown(coordinate, 0, account.pw_gid)
    coordinate.chmod(0o2770)
    for name in ("jobs.lock", "copy.request", "export.request", "catalog.request"):
        path = coordinate / name
        with path.open("x") as body:
            if name.endswith(".request"):
                json.dump({"expires": 0}, body)
        os.chown(path, 0, account.pw_gid)
        path.chmod(0o660)
    state = Path("/mnt/HC_Volume_106755700/greenmind-archive-acceptance") / revision[:12]
    state.mkdir(parents=True, mode=0o700)
    source = Path("/home/traver/greenmind-archive-production/33fe395/compose.json")
    config = json.loads(source.read_text())
    worker = config["services"]["export-worker"]
    worker["volumes"].append(str(runtime / "backend/app") + ":/app/app:ro")
    worker["volumes"].append(str(coordinate) + ":/archive-coordination:ro")
    worker["environment"] = dict(worker.get("environment", {}), RAW_ARCHIVE_COORDINATION_DIR="/archive-coordination", RAW_ARCHIVE_COORDINATION_LEASE_EXTERNAL="true")
    worker["mem_limit"] = "64m"
    config["name"] = "gm-archive-jobs-" + revision[:12]
    config["services"] = {"export-worker": worker}
    compose = state / "exports.json"
    compose.write_text(json.dumps(config, indent=2) + "\n")
    compose.chmod(0o600)
    py = "/opt/greenmind/raw-copy/20260925/venv/bin/python"
    overrides = {}
    values = {
        "greenmind-raw-copy.service": f"[Service]\nEnvironment=PYTHONPATH={runtime}/backend\nEnvironment=RAW_ARCHIVE_COORDINATION_DIR={coordinate}\nReadWritePaths={coordinate}\nSuccessExitStatus=75\nExecStart=\nExecStart={py} {runtime}/deploy/raw-archive/optimized.py\n",
        "greenmind-archive-export.service": f"[Service]\nUser=root\nGroup={account.pw_gid}\nEnvironment=PYTHONPATH={runtime}/backend\nEnvironment=RAW_ARCHIVE_COORDINATION_DIR={coordinate}\nMemoryMax=128M\nSuccessExitStatus=75\nExecStart=\nExecStart={py} {runtime}/deploy/raw-archive/acceptance/dispatch-export.py --state /mnt/HC_Volume_106755700/greenmind-archive-exports/33fe395 --compose {compose}\n",
    }
    try:
        for unit, text in values.items():
            directory = Path("/etc/systemd/system") / (unit + ".d")
            directory.mkdir(exist_ok=True)
            path = directory / ("zz-archive-acceptance-" + revision[:12] + ".conf")
            with path.open("x") as body:
                body.write(text)
            path.chmod(0o644)
            overrides[unit] = {"path": str(path), "sha256": digest(path)}
        subprocess.run(["systemctl", "daemon-reload"], check=True, timeout=10)
        for unit in values:
            actual = subprocess.check_output(["systemctl", "show", unit, "--property=ExecStart", "--value"], text=True, timeout=4)
            assert str(runtime) in actual
        assert protected() == before
    except Exception:
        for item in overrides.values():
            path = Path(item["path"])
            assert digest(path) == item["sha256"], "Inspect a concurrently changed override"
            path.rename(state / (path.name + ".reverted"))
        subprocess.run(["systemctl", "daemon-reload"], check=True, timeout=10)
        raise
    manifest = {"revision": revision, "runtime": str(runtime), "state": str(state), "protected": before, "overrides": overrides, "receivers_unchanged": True, "destructive_flags_enabled": False, "deleted_files": 0, "applies_to_next_scheduled_run": True}
    result = state / "installation.json"
    result.write_text(json.dumps(manifest, indent=2) + "\n")
    result.chmod(0o600)
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
