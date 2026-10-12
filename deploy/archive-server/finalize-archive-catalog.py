"""One bounded full-catalog finalization; no public routes or repeating schedules."""

import argparse
import hashlib
import json
import os
import pwd
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

STATE = Path("/var/lib/greenmind/archive/catalog-preparation-20261009")
PYTHON = "/opt/greenmind/archive-python-20261008-v4/bin/python3"


def main():
    assert os.geteuid() == 0 and os.uname().nodename == "ubuntu-4gb-fsn1-1"
    parser = argparse.ArgumentParser(description=__doc__)
    for kind in ("ledger", "gateway", "direct"):
        parser.add_argument("--" + kind + "-stage", type=Path, required=True)
    parser.add_argument("--quarantine", type=Path, required=True)
    args = parser.parse_args()
    runtime = Path(__file__).resolve().parents[2]
    for name, digest in json.loads((runtime / "files.json").read_text()).items():
        assert hashlib.sha256((runtime / name).read_bytes()).hexdigest() == digest
    account = pwd.getpwnam("greenmind-archive-copy")
    inputs = [
        getattr(args, kind + "_stage") for kind in ("ledger", "gateway", "direct")
    ]
    inputs.append(args.quarantine)
    for path in inputs:
        assert path.is_absolute() and STATE in path.parents and path.exists()
        assert path.resolve() == path and path.stat().st_uid == account.pw_uid
        assert not path.stat().st_mode & 0o077
    for path in inputs[:-1]:
        proof = path / "stage.json"
        assert proof.is_file() and not proof.is_symlink()
        assert (
            proof.stat().st_uid == account.pw_uid and not proof.stat().st_mode & 0o077
        )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    folder = STATE / ("finalize-" + stamp)
    folder.mkdir(mode=0o700)
    os.chown(folder, account.pw_uid, account.pw_gid)
    # Keep the old private access installation and code pointers intact.
    config = folder / "access.env"
    with config.open("xb") as body:
        os.fchmod(body.fileno(), 0o600)
        body.write(Path("/etc/greenmind/archive-offload-v10/access.env").read_bytes())
        body.write(("\nPYTHONPATH=" + str(runtime / "backend") + "\n").encode())
        body.flush()
        os.fsync(body.fileno())
    unit = "greenmind-catalog-finalization-" + stamp.lower()
    properties = {
        "User": account.pw_name,
        "Group": account.pw_name,
        "Slice": "greenmind-archive.slice",
        "EnvironmentFile": str(config),
        "MemoryMax": "384M",
        "MemorySwapMax": "0",
        "CPUQuota": "50%",
        "Nice": "19",
        "IOSchedulingClass": "idle",
        "TasksMax": "32",
        "Type": "oneshot",
        "TimeoutStartSec": "380",
        "SuccessExitStatus": "75",
        "NoNewPrivileges": "true",
        "PrivateTmp": "true",
        "ProtectSystem": "strict",
        "ProtectHome": "true",
        "UMask": "0077",
        "ReadWritePaths": str(folder),
    }
    command = ["systemd-run", "--no-block", "--unit=" + unit]
    command.extend(
        "--property=" + key + "=" + value for key, value in properties.items()
    )
    command.extend([PYTHON, "-m", "app.raw_archive.archive_catalog_finalize"])
    for kind in ("ledger", "gateway", "direct"):
        command.extend(["--" + kind + "-stage", str(getattr(args, kind + "_stage"))])
    command.extend(
        ["--quarantine", str(args.quarantine), "--output", str(folder / "result")]
    )
    subprocess.run(command, check=True, capture_output=True, timeout=15)
    print(
        json.dumps(
            {"unit": unit, "state": str(folder), "scheduled": False, "deleted_files": 0}
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
