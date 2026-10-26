"""Bounded one-off archive-host SQL stages; never install or replace schedules."""

import argparse
import hashlib
import json
import os
import pwd
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[2]
STATE = Path("/var/lib/greenmind/archive/catalog-preparation-20261009")
PYTHON = "/opt/greenmind/archive-python-20261008-v4/bin/python3"


def worker(folder):
    assert os.geteuid() == pwd.getpwnam("greenmind-archive-copy").pw_uid
    assert folder.parent == STATE and folder.is_dir() and not folder.is_symlink()
    assert folder.stat().st_uid == os.geteuid() and not folder.stat().st_mode & 0o077
    started = time.monotonic()
    results = {}
    for kind in ("gateway", "direct"):
        for number in range(4):
            if time.monotonic() - started > 360:
                value = {"status": "paused", "reason": "validation_deadline"}
                break
            result = subprocess.run(
                [PYTHON, "-m", "app.raw_archive.archive_host_catalog", "--kind", kind,
                 "--output", str(folder / kind)],
                check=False, capture_output=True, text=True, timeout=125,
            )
            # Only the redacting entrypoint's bounded JSON is evidence, never stderr.
            assert len(result.stdout) <= 1024 * 1024
            value = json.loads(result.stdout)
            if value.get("status") in ("complete", "reused") and result.returncode != 0:
                raise RuntimeError("Failed source process cannot prove completion")
            with (folder / f"{kind}-try-{number}.json").open("x") as file:
                json.dump(value, file)
            print(json.dumps({"kind": kind, "attempt": number, **value}), flush=True)
            if (value.get("status") == "paused"
                    and value.get("reason") == "archive_job_busy" and number < 3):
                time.sleep(20)  # No SQL connection while waiting for the existing yield.
                continue
            break
        results[kind] = value
        if value.get("status") not in ("complete", "reused"):
            break
    completed = set(results) == {"gateway", "direct"} and all(
        value.get("status") in ("complete", "reused") for value in results.values()
    )
    proof = {
        "status": "metadata_stages_complete" if completed else "incomplete",
        "results": results, "complete_catalog": False,
        "independent_restoration": False, "cross_database_atomic": False,
        "schedules_changed": False, "deleted_files": 0,
    }
    with (folder / "validation.json").open("x") as file:
        json.dump(proof, file, indent=2)
    print(json.dumps(proof), flush=True)
    return 0 if completed else 75


def dispatch():
    assert os.geteuid() == 0
    for name, digest in json.loads((RUNTIME / "files.json").read_text()).items():
        assert hashlib.sha256((RUNTIME / name).read_bytes()).hexdigest() == digest
    account = pwd.getpwnam("greenmind-archive-copy")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    folder = STATE / ("metadata-" + stamp)
    folder.mkdir(mode=0o700)
    os.chown(folder, account.pw_uid, account.pw_gid)
    config = folder / "access.env"
    with config.open("xb") as body:
        os.fchmod(body.fileno(), 0o600)
        body.write(Path("/etc/greenmind/archive-offload-v10/access.env").read_bytes())
        # Match the original source lease/backup ceiling; never relax its 2.4 cap.
        # The archive host's independent 800 MiB/load guard remains unchanged.
        body.write(b"\nRAW_ARCHIVE_SOURCE_MAX_LOAD=2.4\n")
        body.write(("\nPYTHONPATH=" + str(RUNTIME / "backend") + "\n").encode())
        body.flush()
        os.fsync(body.fileno())
    unit = "greenmind-catalog-metadata-validation-" + stamp.lower()
    properties = {
        "User": account.pw_name, "Group": account.pw_name,
        "Slice": "greenmind-archive.slice",
        "EnvironmentFile": str(config),
        "MemoryMax": "384M", "MemorySwapMax": "0", "CPUQuota": "50%",
        "Nice": "19", "IOSchedulingClass": "idle", "TasksMax": "32",
        "TimeoutStartSec": "500", "Type": "oneshot", "SuccessExitStatus": "75",
        "NoNewPrivileges": "true", "PrivateTmp": "true",
        "ProtectSystem": "strict", "ProtectHome": "true", "UMask": "0077",
        "ReadWritePaths": str(folder),
    }
    command = ["systemd-run", "--no-block", "--unit=" + unit]
    command.extend("--property=" + key + "=" + value for key, value in properties.items())
    command.extend([PYTHON, str(Path(__file__).resolve()), "--worker", str(folder)])
    subprocess.run(command, check=True, capture_output=True, timeout=15)
    print(json.dumps({"unit": unit, "state": str(folder), "deleted_files": 0}))


def main():
    assert os.uname().nodename == "ubuntu-4gb-fsn1-1"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path)
    args = parser.parse_args()
    if args.worker:
        return worker(args.worker)
    dispatch()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001 - redact all credential-bearing driver errors
        print(json.dumps({"status": "blocked", "error_type": type(error).__name__, "deleted_files": 0}))
        raise SystemExit(1) from None
