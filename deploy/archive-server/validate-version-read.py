"""One private, bounded diagnostic worker; no upload keys, SQL or mutations."""

import argparse
import hashlib
import json
import os
import pwd
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[2]
PYTHON = "/opt/greenmind/archive-python-20261008-v4/bin/python3"
STATE = Path("/var/lib/greenmind/archive/version-read-validation-20261010")
REQUEST = Path(
    "/var/lib/greenmind/archive-reader-validation-20261009/20261009T083411600076/requests.json"
)
HEALTH_FIELDS = {
    "RAW_ARCHIVE_WORKER_ROLE",
    "RAW_ARCHIVE_HEALTH_URLS",
    "RAW_ARCHIVE_SOURCE_MOUNTS",
    "RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB",
    "RAW_ARCHIVE_MAX_HOST_LOAD",
    "RAW_ARCHIVE_SOURCE_PROBE_URL",
    "RAW_ARCHIVE_SOURCE_PROBE_TOKEN_FILE",
    "RAW_ARCHIVE_SOURCE_ID",
    "RAW_ARCHIVE_SOURCE_MIN_MEMORY_MIB",
    "RAW_ARCHIVE_SOURCE_MAX_LOAD",
}


def env(path):
    assert not path.is_symlink() and path.is_file() and not path.stat().st_mode & 7
    return dict(
        x.split("=", 1)
        for x in path.read_text().splitlines()
        if x and not x.startswith("#")
    )


def private(path, data, uid, gid):
    with path.open("xb") as file:
        os.fchmod(file.fileno(), 0o600)
        os.fchown(file.fileno(), uid, gid)
        file.write(data)
        file.flush()
        os.fsync(file.fileno())


def worker(folder, *, targets=False):
    from app.raw_archive.diagnostics import client_from_environment
    from app.raw_archive.runner import health_probe
    if targets:
        from app.raw_archive.target_read_probe import inspect
    else:
        from app.raw_archive.version_read_probe import inspect

    assert os.geteuid() == pwd.getpwnam("greenmind-archive-copy").pw_uid
    assert folder.parent == STATE and folder.resolve() == folder
    assert folder.stat().st_uid == os.getuid() and not folder.stat().st_mode & 0o077
    assert not any(
        x.startswith("RAW_ARCHIVE_SFTP_") or x.endswith("DATABASE_URL")
        for x in os.environ
    )
    healthy = health_probe()
    client = client_from_environment()
    try:
        proof = inspect(
            client, json.loads((folder / "requests.json").read_text()), healthy
        )
        proof["checked_at"] = datetime.now(UTC).isoformat()
        private(
            folder / "verified.json",
            json.dumps(proof, indent=2).encode(),
            os.getuid(),
            os.getgid(),
        )
        print(json.dumps(proof))
    finally:
        client.close()
        healthy.close()


def dispatch(*, targets=False):
    assert os.geteuid() == 0
    for name, sha in json.loads((RUNTIME / "files.json").read_text()).items():
        assert hashlib.sha256((RUNTIME / name).read_bytes()).hexdigest() == sha
    account = pwd.getpwnam("greenmind-archive-copy")
    assert not STATE.is_symlink()
    STATE.mkdir(mode=0o700, exist_ok=True)
    assert not STATE.stat().st_mode & 0o077
    os.chown(STATE, account.pw_uid, account.pw_gid)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    folder = STATE / stamp
    folder.mkdir(mode=0o700)
    os.chown(folder, account.pw_uid, account.pw_gid)
    old = env(Path("/etc/greenmind/archive-offload-v10/access.env"))
    values = {k: old[k] for k in HEALTH_FIELDS}
    assert int(values["RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB"]) >= 800
    assert int(values["RAW_ARCHIVE_SOURCE_MIN_MEMORY_MIB"]) >= 640
    diagnostic = env(Path("/etc/greenmind/archive-diagnostic-read-20261010/read.env"))
    assert set(diagnostic) == {
        "RAW_ARCHIVE_DIAGNOSTIC_" + k for k in ("ENDPOINT", "ACCESS_KEY", "SECRET_KEY")
    } | {"RAW_ARCHIVE_DIRECT_S3_BUCKET"}
    values.update(
        diagnostic,
        PYTHONPATH=str(RUNTIME / "backend"),
        PYTHONHOME="/opt/greenmind/archive-python-20261008-v4",
        LD_LIBRARY_PATH="/opt/greenmind/archive-python-20261008-v4/lib",
        PYTHONDONTWRITEBYTECODE="1",
        RAW_ARCHIVE_DELETE_ENABLED="false",
    )
    assert not REQUEST.is_symlink() and REQUEST.stat().st_size <= 16384
    private(
        folder / "requests.json", REQUEST.read_bytes(), account.pw_uid, account.pw_gid
    )
    config = folder / "read.env"
    private(
        config,
        "".join(k + "=" + v + "\n" for k, v in values.items()).encode(),
        account.pw_uid,
        account.pw_gid,
    )
    props = {
        "User": account.pw_name,
        "Group": account.pw_name,
        "Slice": "greenmind-archive.slice",
        "EnvironmentFile": str(config),
        "MemoryMax": "192M",
        "MemorySwapMax": "0",
        "CPUQuota": "25%",
        "Nice": "19",
        "IOSchedulingClass": "idle",
        "TasksMax": "24",
        "Type": "oneshot",
        "TimeoutStartSec": "90",
        "SuccessExitStatus": "75",
        "NoNewPrivileges": "true",
        "PrivateTmp": "true",
        "ProtectSystem": "strict",
        "ProtectHome": "true",
        "ReadWritePaths": str(folder),
        "UMask": "0077",
    }
    unit = "greenmind-version-read-validation-" + stamp.lower()
    cmd = ["systemd-run", "--no-block", "--unit=" + unit]
    cmd.extend("--property=" + k + "=" + v for k, v in props.items())
    cmd.extend([PYTHON, str(Path(__file__).resolve()), "--worker", str(folder)])
    if targets:
        cmd.append("--targets")
    subprocess.run(cmd, check=True, capture_output=True, timeout=12)
    print(
        json.dumps(
            {"unit": unit, "state": str(folder), "scheduled": False, "deleted_files": 0}
        )
    )


def main():
    assert os.uname().nodename == "ubuntu-4gb-fsn1-1"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--targets", action="store_true",
                        help="Read-only target checks against original sample receipts")
    args = parser.parse_args()
    if args.worker:
        worker(args.worker, targets=args.targets)
    else:
        dispatch(targets=args.targets)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001 - credential-safe boundary
        paused = type(error).__name__ == "SafetyPause"
        value = {
            "status": "paused" if paused else "blocked",
            "error_type": type(error).__name__,
            "deleted_files": 0,
        }
        if paused:
            value["reason"] = error.code
        print(json.dumps(value))
        sys.exit(75 if paused else 1)
