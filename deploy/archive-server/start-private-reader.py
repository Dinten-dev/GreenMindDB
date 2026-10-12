"""Bounded start of the fixed, unpublished API and independent read-only broker."""

import hashlib
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import urlopen

ROOT = Path("/mnt/HC_Volume_106755700/greenmind-delete-preparation/reader-20261010-v22")
RUNTIME = Path("/opt/greenmind/archive-preparation/20261010-v23")
UNIT = "greenmind-archive-read-candidate-20261010"
CONTAINERS = (
    "gm-zones-production-2e687bec010a-application-1",
    "gm-direct-production-direct-api-1", "greenminddb-backend-1",
)
PROXY_SHA = "f4e7b516852a52152e03685f59ec1b396c5634af269114e5c81e9c5f28e95436"


def command(args, timeout=10):
    return subprocess.run(args, capture_output=True, text=True, check=True, timeout=timeout)


def protected():
    entries = json.loads(command(["docker", "inspect", *CONTAINERS]).stdout)
    return [{"name": row["Name"], "started": row["State"]["StartedAt"],
             "restarts": row["RestartCount"], "running": row["State"]["Running"]} for row in entries]


def reserve():
    memory = int(next(line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines()
                      if line.startswith("MemAvailable:"))) / 1024
    load = os.getloadavg()[0]
    return memory >= 896 and load <= 2.4, {"memory_mib": round(memory, 1), "load": round(load, 3)}


def healthy(port):
    try:
        with urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


def checked_configuration():
    assert hashlib.sha256(Path("/etc/nginx/sites-available/greenmind-prod").read_bytes()).hexdigest() == PROXY_SHA
    for name, digest in json.loads((RUNTIME / "files.json").read_text()).items():
        assert hashlib.sha256((RUNTIME / name).read_bytes()).hexdigest() == digest
    config = json.loads((ROOT / "compose.json").read_text())
    assert config["name"] == "gm-archive-reader-20261010" and set(config["services"]) == {"api"}
    api = config["services"]["api"]
    assert api["ports"] == ["127.0.0.1:8141:8000"]
    assert api["user"] == "996:986" and api["mem_limit"] == api["memswap_limit"] == "192m"
    assert api["read_only"] is True and api["cap_drop"] == ["ALL"] and api["cpus"] == 0.1
    assert str(RUNTIME / "backend/app") + ":/app/app:ro" in api["volumes"]
    for filename in ("read.env", "broker.env"):
        path = ROOT / filename
        assert path.is_file() and not path.is_symlink() and path.stat().st_uid == 0
        assert not path.stat().st_mode & 0o007
        values = dict(line.split("=", 1) for line in path.read_text().splitlines()
                      if line and not line.startswith("#"))
        assert not any(key.startswith("RAW_ARCHIVE_SFTP_") for key in values)
        assert values.get("RAW_ARCHIVE_DELETE_ENABLED") == "false"
        if filename == "broker.env":
            assert values["RAW_ARCHIVE_READ_ONLY_SFTP_USER"] == "u676312-sub1"
            assert values["RAW_ARCHIVE_READ_ONLY_SFTP_HOST"] == "u676312-sub1.your-storagebox.de"
    return ROOT / "compose.json"


def main():
    assert os.geteuid() == 0 and os.uname().nodename == "Biolingo"
    compose = checked_configuration()
    baseline = protected()
    assert all(item["running"] for item in baseline)
    assert not healthy(8141), "An existing candidate must be inspected, never overwritten"
    # Preserve the failed candidate's state before restarting this isolated pair.
    attempt = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    previous = command(["docker", "inspect", "gm-archive-reader-20261010-api-1"]).stdout
    with (ROOT / ("previous-private-" + attempt + ".json")).open("x") as body:
        os.fchmod(body.fileno(), 0o600)
        json.dump({"at": attempt, "state": json.loads(previous)[0]["State"]}, body)
    command(["systemctl", "stop", UNIT])
    subprocess.run(["systemctl", "reset-failed", UNIT], check=False, capture_output=True, timeout=10)
    socket = Path("/run/greenmind-archive-read-candidate/broker.sock")
    if socket.exists():
        assert socket.is_socket() and socket.lstat().st_uid == 996
    deadline = time.monotonic() + 240
    while True:
        ready, resources = reserve()
        if ready:
            break
        if time.monotonic() >= deadline:
            print(json.dumps({"status": "paused", "reason": "source_headroom", **resources,
                              "started": False, "public_routes_changed": False, "deleted_files": 0}))
            return 75
        time.sleep(5)  # No SQL connections or new services during waiting.
    assert all(healthy(port) for port in (8120, 8003, 8000, 8140))
    started = False
    try:
        props = {
            "User": "greenmind-raw-copy", "Group": "greenmind-raw-copy",
            "EnvironmentFile": str(ROOT / "broker.env"), "MemoryMax": "64M",
            "MemorySwapMax": "0", "CPUQuota": "5%", "Nice": "19",
            "IOSchedulingClass": "idle", "TasksMax": "16", "NoNewPrivileges": "true",
            "PrivateTmp": "true", "ProtectSystem": "strict", "ProtectHome": "read-only",
            "UMask": "0077", "RuntimeMaxSec": "1800",
            "ReadWritePaths": "/run/greenmind-archive-read-candidate /mnt/HC_Volume_106755700/greenmind-archive-reader-candidate",
        }
        args = ["systemd-run", "--unit=" + UNIT]
        args += ["--property=" + key + "=" + value for key, value in props.items()]
        args += ["/opt/greenmind/raw-copy/20260925/venv/bin/python", "-m", "app.raw_archive.read_broker"]
        command(args)
        started = True
        command(["docker", "compose", "-f", str(compose), "up", "-d", "--no-build", "--no-deps", "api"], timeout=35)
        until = time.monotonic() + 35
        while not healthy(8141) and time.monotonic() < until:
            time.sleep(1)
        assert healthy(8141) and Path("/run/greenmind-archive-read-candidate/broker.sock").is_socket()
        assert protected() == baseline and all(healthy(p) for p in (8120, 8003, 8000, 8140))
        assert reserve()[1]["memory_mib"] >= 640
        assert hashlib.sha256(Path("/etc/nginx/sites-available/greenmind-prod").read_bytes()).hexdigest() == PROXY_SHA
        proof = {"status": "running_unpublished", "at": datetime.now(UTC).isoformat(),
                 "resources": reserve()[1], "receivers_unchanged": True,
                 "public_routes_changed": False, "accepted": False, "deleted_files": 0}
        with (ROOT / ("started-private-" + attempt + ".json")).open("x") as body:
            os.fchmod(body.fileno(), 0o600)
            json.dump(proof, body)
        print(json.dumps(proof))
        return 0
    except Exception as error:  # noqa: BLE001 - redact credential-bearing runtime failures
        if started:
            try:
                subprocess.run(["docker", "compose", "-f", str(compose), "stop", "api"], check=False, capture_output=True, timeout=25)
            finally:
                subprocess.run(["systemctl", "stop", UNIT], check=False, capture_output=True, timeout=15)
        print(json.dumps({"status": "blocked", "error_type": type(error).__name__,
                          "public_routes_changed": False, "deleted_files": 0}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
