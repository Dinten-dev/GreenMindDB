"""Dispatch one journal-only checkpoint within original source safety reserves."""

import hashlib
import json
import os
import pwd
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def main():
    assert os.geteuid() == 0 and os.uname().nodename == "Biolingo"
    runtime = Path(__file__).resolve().parents[2]
    for name, digest in json.loads((runtime / "files.json").read_text()).items():
        assert hashlib.sha256((runtime / name).read_bytes()).hexdigest() == digest
    available = (
        next(
            int(x.split()[1])
            for x in Path("/proc/meminfo").read_text().splitlines()
            if x.startswith("MemAvailable:")
        )
        / 1024
    )
    # Preserve 640 MiB even if the entire new 128 MiB worker budget is used.
    if available < 768 or os.getloadavg()[0] > 2.4:
        print(
            json.dumps(
                {
                    "status": "paused",
                    "reason": "source_startup_headroom",
                    "available_mib": round(available, 1),
                    "required_before_start_mib": 768,
                    "started": False,
                    "deleted_files": 0,
                }
            )
        )
        return 75
    account = pwd.getpwnam("greenmind-raw-copy")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    state = Path("/mnt/HC_Volume_106755700/greenmind-feature-backup") / (
        "ledger-" + stamp
    )
    state.mkdir(mode=0o700)
    os.chown(state, account.pw_uid, account.pw_gid)
    unit = "greenmind-source-ledger-validation-" + stamp.lower()
    values = {
        "PYTHONPATH": str(runtime / "backend"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "RAW_ARCHIVE_ENVIRONMENT": "production",
        "RAW_ARCHIVE_COORDINATION_DIR": "/var/lib/greenmind-archive-coordination",
        "RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB": "640",
        "RAW_ARCHIVE_MAX_HOST_LOAD": "2.4",
        "RAW_ARCHIVE_SOURCE_MOUNTS": "/,/mnt/HC_Volume_106755700",
        "RAW_ARCHIVE_HEALTH_URLS": ",".join(
            f"http://127.0.0.1:{p}/health" for p in (8120, 8003, 8000)
        ),
    }
    values.update(
        {
            key: "false"
            for key in (
                "RAW_ARCHIVE_ENABLED",
                "RAW_ARCHIVE_DELETE_ENABLED",
                "RAW_ARCHIVE_LEGACY_NULL_ACCEPTED",
                "RETENTION_ENABLED",
                "DIRECT_RETENTION_ENABLED",
                "VISUAL_PRUNE_ENABLED",
            )
        }
    )
    properties = {
        "User": account.pw_name,
        "Group": account.pw_name,
        "MemoryMax": "128M",
        "MemorySwapMax": "0",
        "CPUQuota": "10%",
        "Nice": "19",
        "IOSchedulingClass": "idle",
        "TasksMax": "24",
        "TimeoutStartSec": "140",
        "Type": "oneshot",
        "SuccessExitStatus": "75",
        "MemoryAccounting": "true",
        "NoNewPrivileges": "true",
        "PrivateTmp": "true",
        "ProtectSystem": "strict",
        "ProtectHome": "true",
        "UMask": "0077",
        "ReadWritePaths": str(state) + " /var/lib/greenmind-archive-coordination",
    }
    command = ["systemd-run", "--no-block", "--unit=" + unit]
    command.extend(
        "--property=" + key + "=" + value for key, value in properties.items()
    )
    command.extend("--setenv=" + key + "=" + value for key, value in values.items())
    command.extend(
        [
            "/opt/greenmind/raw-copy/20260925/venv/bin/python",
            "-m",
            "app.raw_archive.source_ledger_once",
            "--output",
            str(state / "stage"),
        ]
    )
    subprocess.run(command, check=True, capture_output=True, timeout=15)
    print(
        json.dumps(
            {"unit": unit, "state": str(state), "deleted_files": 0, "scheduled": False}
        )
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error_type": type(error).__name__,
                    "deleted_files": 0,
                }
            )
        )
        raise SystemExit(1) from None
