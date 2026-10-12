"""One archive-host-only journal conversion, no source connections or credentials."""

import argparse
import hashlib
import json
import os
import pwd
import subprocess
from datetime import UTC, datetime
from pathlib import Path

STATE = Path("/var/lib/greenmind/archive/catalog-preparation-20261009")


def main():
    assert os.geteuid() == 0 and os.uname().nodename == "ubuntu-4gb-fsn1-1"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    runtime = Path(__file__).resolve().parents[2]
    for name, digest in json.loads((runtime / "files.json").read_text()).items():
        assert hashlib.sha256((runtime / name).read_bytes()).hexdigest() == digest
    account = pwd.getpwnam("greenmind-archive-copy")
    root = args.input
    assert root.parent == STATE and root.resolve() == root and root.is_dir()
    for path in (root, root / "source-ledger.sqlite3", root / "journal-proof.json"):
        assert path.resolve() == path and path.stat().st_uid == account.pw_uid
        assert not path.stat().st_mode & 0o077
    available = (
        next(
            int(x.split()[1])
            for x in Path("/proc/meminfo").read_text().splitlines()
            if x.startswith("MemAvailable:")
        )
        / 1024
    )
    assert available >= 1056 and os.getloadavg()[0] <= 2
    unit = (
        "greenmind-journal-conversion-"
        + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f").lower()
    )
    props = {
        "User": account.pw_name,
        "Group": account.pw_name,
        "Slice": "greenmind-archive.slice",
        "MemoryMax": "256M",
        "MemorySwapMax": "0",
        "CPUQuota": "50%",
        "Nice": "19",
        "IOSchedulingClass": "idle",
        "TasksMax": "24",
        "Type": "oneshot",
        "TimeoutStartSec": "120",
        "SuccessExitStatus": "75",
        "NoNewPrivileges": "true",
        "PrivateTmp": "true",
        "ProtectSystem": "strict",
        "ProtectHome": "true",
        "UMask": "0077",
        "ReadWritePaths": str(root),
    }
    env = {
        "PYTHONPATH": str(runtime / "backend"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "RAW_ARCHIVE_WORKER_ROLE": "archive",
        "RAW_ARCHIVE_ENVIRONMENT": "production",
        "RAW_ARCHIVE_LEASE_HOST": "188.245.247.156",
        "RAW_ARCHIVE_SOURCE_MAX_LOAD": "2",
        "RAW_ARCHIVE_SOURCE_MIN_MEMORY_MIB": "640",
        "RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB": "800",
    }
    for key in (
        "RAW_ARCHIVE_ENABLED",
        "RAW_ARCHIVE_DELETE_ENABLED",
        "RAW_ARCHIVE_LEGACY_NULL_ACCEPTED",
        "RETENTION_ENABLED",
        "DIRECT_RETENTION_ENABLED",
        "VISUAL_PRUNE_ENABLED",
    ):
        env[key] = "false"
    command = ["systemd-run", "--no-block", "--unit=" + unit]
    command += ["--property=" + k + "=" + v for k, v in props.items()]
    command += ["--setenv=" + k + "=" + v for k, v in env.items()]
    command += [
        "/opt/greenmind/archive-python-20261008-v4/bin/python3",
        "-m",
        "app.raw_archive.ledger_checkpoint_stage",
        "--source",
        str(root / "source-ledger.sqlite3"),
        "--proof",
        str(root / "journal-proof.json"),
        "--output",
        str(root / "stage"),
    ]
    subprocess.run(command, check=True, capture_output=True, timeout=10)
    print(
        json.dumps(
            {
                "unit": unit,
                "state": str(root),
                "source_connection": False,
                "credentials_loaded": False,
                "deleted_files": 0,
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001 - credential-safe boundary
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
