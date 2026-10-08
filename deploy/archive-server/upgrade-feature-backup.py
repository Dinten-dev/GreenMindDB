"""Hash-guard only the new feature unit; preserve its original package/config."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2]
OLD = Path("/opt/greenmind/feature-backup/20261008")
NEW = Path("/opt/greenmind/feature-backup/20261008-v2")
ENV = Path("/etc/greenmind/feature-backup/backup.env")
NEW_ENV = ENV.with_name("backup-v2.env")
UNIT = Path("/etc/systemd/system/greenmind-feature-backup.service")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-unit", required=True)
    parser.add_argument("--expected-env", required=True)
    args = parser.parse_args()
    assert os.geteuid() == 0
    assert sha(UNIT) == args.expected_unit and sha(ENV) == args.expected_env
    assert not NEW.exists() and not NEW_ENV.exists()
    state = subprocess.check_output(
        [
            "systemctl",
            "show",
            "greenmind-feature-backup.service",
            "-p",
            "ActiveState",
            "--value",
        ],
        text=True,
        timeout=8,
    ).strip()
    assert state in {"inactive", "failed"}
    shutil.copytree(SOURCE / "backend", NEW / "backend")
    (NEW / "deploy/archive-server").mkdir(parents=True)
    shutil.copyfile(
        SOURCE / "deploy/archive-server/feature-backup-daily.py",
        NEW / "deploy/archive-server/feature-backup-daily.py",
    )
    for path in NEW.rglob("*"):
        assert not path.is_symlink()
        os.chown(path, 0, 0)
        path.chmod(0o755 if path.is_dir() else 0o644)
    body = ENV.read_text()
    assert body.count("PYTHONPATH=" + str(OLD / "backend")) == 1
    with NEW_ENV.open("x") as target:
        os.fchmod(target.fileno(), 0o640)
        os.fchown(target.fileno(), 0, ENV.stat().st_gid)
        target.write(
            body.replace(
                "PYTHONPATH=" + str(OLD / "backend"),
                "PYTHONPATH=" + str(NEW / "backend"),
            )
        )
    previous = UNIT.read_text()
    assert previous.count(str(OLD)) == 1 and previous.count(str(ENV)) == 1
    replacement = previous.replace(str(OLD), str(NEW)).replace(str(ENV), str(NEW_ENV))
    backup = NEW / "original-feature.service"
    backup.write_text(previous)
    pending = UNIT.with_name(".greenmind-feature-backup.service.v2")
    with pending.open("x") as target:
        os.fchmod(target.fileno(), 0o644)
        target.write(replacement)
        target.flush()
        os.fsync(target.fileno())
    checked = NEW / "units/greenmind-feature-backup.service"
    checked.parent.mkdir()
    checked.write_text(replacement)
    subprocess.run(
        ["systemd-analyze", "verify", str(checked)],
        check=True,
        capture_output=True,
        timeout=10,
    )
    assert sha(UNIT) == args.expected_unit and sha(ENV) == args.expected_env
    os.replace(pending, UNIT)
    try:
        subprocess.run(["systemctl", "daemon-reload"], check=True, timeout=10)
    except BaseException:
        UNIT.write_text(previous)
        subprocess.run(["systemctl", "daemon-reload"], check=True, timeout=10)
        raise
    print(
        json.dumps(
            {
                "status": "upgraded",
                "runtime": str(NEW),
                "deletion_enabled": False,
                "receivers_modified": False,
            }
        )
    )


if __name__ == "__main__":
    main()
