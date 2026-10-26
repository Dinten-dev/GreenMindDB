"""One private, constrained preparation attempt; no timer or receiver changes."""

import argparse
import hashlib
import json
import os
import pwd
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def read_env(path):
    assert path.is_file() and not path.is_symlink() and not path.stat().st_mode & 0o007
    return dict(
        line.split("=", 1)
        for line in path.read_text().splitlines()
        if line and not line.startswith("#")
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("feature", "catalog"), required=True)
    args = parser.parse_args()
    assert os.geteuid() == 0 and os.uname().nodename == "Biolingo"
    runtime = Path(__file__).resolve().parents[2]
    hashes = json.loads((runtime / "files.json").read_text())
    assert all(
        hashlib.sha256((runtime / p).read_bytes()).hexdigest() == sha
        for p, sha in hashes.items()
    )
    account = pwd.getpwnam("greenmind-raw-copy")
    values = read_env(Path("/etc/greenmind/feature-backup/backup-v3.env"))
    readonly = read_env(Path("/etc/greenmind/archive-readonly-20261007/broker.env"))
    assert all(key.startswith("RAW_ARCHIVE_READ_ONLY_SFTP_") for key in readonly)
    values.update(readonly)
    assert values["RAW_ARCHIVE_SFTP_USER"] == "u676312"
    assert values["RAW_ARCHIVE_READ_ONLY_SFTP_USER"] == "u676312-sub1"
    assert all(
        values[key] == "false"
        for key in (
            "RAW_ARCHIVE_ENABLED",
            "RAW_ARCHIVE_DELETE_ENABLED",
            "RETENTION_ENABLED",
            "DIRECT_RETENTION_ENABLED",
            "VISUAL_PRUNE_ENABLED",
        )
    )
    suffix = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    root = Path("/mnt/HC_Volume_106755700/greenmind-feature-backup") / (
        "preparation-" + suffix
    )
    root.mkdir(mode=0o700)
    os.chown(root, account.pw_uid, account.pw_gid)
    config = Path("/etc/greenmind/feature-backup") / ("preparation-" + suffix + ".env")
    values.update(
        PYTHONPATH=str(runtime / "backend"), RAW_ARCHIVE_FEATURE_STATE=str(root)
    )
    with config.open("x") as body:
        os.fchmod(body.fileno(), 0o640)
        os.fchown(body.fileno(), 0, account.pw_gid)
        body.write("".join(key + "=" + value + "\n" for key, value in values.items()))
    unit = "greenmind-" + args.kind + "-preparation-" + suffix.lower()
    command = [
        "/usr/bin/systemd-run",
        "--unit=" + unit,
        "--property=Type=oneshot",
        "--property=User=greenmind-raw-copy",
        "--property=Group=greenmind-raw-copy",
        "--property=MemoryMax=128M",
        "--property=MemorySwapMax=0",
        "--property=CPUQuota=10%",
        "--property=Nice=19",
        "--property=IOSchedulingClass=idle",
        "--property=TimeoutStartSec=350",
        "--property=TasksMax=32",
        "--property=MemoryAccounting=true",
        "--property=NoNewPrivileges=true",
        "--property=PrivateTmp=true",
        "--property=ProtectSystem=strict",
        "--property=ProtectHome=read-only",
        "--property=UMask=0077",
        "--property=SuccessExitStatus=75",
        "--property=EnvironmentFile=" + str(config),
        "--property=ReadWritePaths="
        + str(root)
        + " /var/lib/greenmind-archive-coordination",
        "/opt/greenmind/raw-copy/20260925/venv/bin/python",
    ]
    if args.kind == "feature":
        command += [
            str(runtime / "deploy/archive-server/feature-backup-daily.py"),
            "--manual-validation",
        ]
    else:
        command += [
            "-m",
            "app.raw_archive.source_catalog_once",
            "--output",
            str(root / "catalog"),
        ]
    subprocess.run(command, check=True, timeout=15, capture_output=True)
    proof = {
        "unit": unit,
        "state": str(root),
        "kind": args.kind,
        "scheduled": False,
        "receiver_changes": False,
        "deleted_files": 0,
        "deletion_enabled": False,
    }
    with (root / "dispatch.json").open("x") as body:
        os.fchmod(body.fileno(), 0o600)
        json.dump(proof, body)
    print(json.dumps(proof))


if __name__ == "__main__":
    main()
