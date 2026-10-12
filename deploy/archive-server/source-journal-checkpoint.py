"""32 MiB source-only SQLite checkpoint; compression belongs on the archive host."""

import argparse
import fcntl
import hashlib
import json
import os
import pwd
import shutil
import sqlite3
import stat
import subprocess
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import urlopen

STATE = Path("/mnt/HC_Volume_106755700/greenmind-feature-backup")
SOURCE = Path("/var/lib/greenmind-raw-copy/archive.sqlite3")
COORD = Path("/var/lib/greenmind-archive-coordination")
FLAGS = (
    "RAW_ARCHIVE_ENABLED",
    "RAW_ARCHIVE_DELETE_ENABLED",
    "RAW_ARCHIVE_LEGACY_NULL_ACCEPTED",
    "RETENTION_ENABLED",
    "DIRECT_RETENTION_ENABLED",
    "VISUAL_PRUNE_ENABLED",
)


class Pause(RuntimeError):
    pass


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as body:
        for chunk in iter(lambda: body.read(65536), b""):
            value.update(chunk)
    return value.hexdigest()


def available():
    return (
        next(
            int(x.split()[1])
            for x in Path("/proc/meminfo").read_text().splitlines()
            if x.startswith("MemAvailable:")
        )
        / 1024
    )


def guard():
    if available() < 640:
        raise Pause("source_memory")
    if os.getloadavg()[0] > 2.4:
        raise Pause("source_load")
    for mount in ("/", "/mnt/HC_Volume_106755700"):
        usage = shutil.disk_usage(mount)
        if usage.free < max(2 * 1024**3, usage.total * 0.05):
            raise Pause("source_space")
    gateway = Path('/etc/greenmind/receiver-health/gateway.json')
    ports = (8120,)
    if gateway.exists():
        info = gateway.stat()
        value = json.loads(gateway.read_bytes())
        if (gateway.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022
                or value != {'schema': 1, 'ports': [8120, 8152]}):
            raise RuntimeError('Untrusted receiver configuration')
        ports = (8120, 8152)
    for port in (*ports, 8003, 8000):
        with urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
            if response.status != 200:
                raise Pause("source_receiver_health")


def private_fd(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o660)
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid not in (0, os.getuid())
        or info.st_mode & 7
    ):
        os.close(fd)
        raise RuntimeError("Untrusted shared coordination file")
    return os.fdopen(fd, "r+")


@contextmanager
def shared_lease():
    info = COORD.stat()
    if COORD.is_symlink() or info.st_uid not in (0, os.getuid()) or info.st_mode & 7:
        raise RuntimeError("Untrusted coordination directory")
    with private_fd(COORD / "jobs.lock") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            with private_fd(COORD / "catalog.request") as request:
                fcntl.flock(request, fcntl.LOCK_EX)
                request.seek(0)
                json.dump({"expires": time.time() + 300}, request)
                request.truncate()
                request.flush()
            raise Pause("archive_job_busy") from None
        with private_fd(COORD / "catalog.request") as request:
            fcntl.flock(request, fcntl.LOCK_EX)
            request.seek(0)
            json.dump({"expires": 0}, request)
            request.truncate()
            request.flush()
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def checkpoint(output, *, healthy=guard):
    started = time.monotonic()
    last = float("-inf")

    def check(*_):
        nonlocal last
        now = time.monotonic()
        if now - started >= 110:
            raise Pause("journal_deadline")
        if now - last >= 2:
            healthy()
            last = time.monotonic()
        if output.exists() and output.stat().st_size > 512 * 1024**2:
            raise Pause("journal_size")

    with shared_lease():
        check()
        if SOURCE.is_symlink() or output.exists():
            raise RuntimeError("Preserve source and previous checkpoints")
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        began = datetime.now(UTC).isoformat()
        origin = sqlite3.connect(SOURCE.as_uri() + "?mode=ro", uri=True, timeout=2)
        target = sqlite3.connect(output)
        try:
            origin.execute("PRAGMA query_only=ON")
            origin.execute("PRAGMA cache_size=-512")
            target.execute("PRAGMA cache_size=-512")
            origin.backup(target, pages=32, progress=check, sleep=0.01)
            paused = None

            def sql_progress():
                nonlocal paused
                try:
                    check()
                except Pause as error:
                    # SQLite converts callback exceptions into OperationalError.
                    # Keep the real safety reason and interrupt the query.
                    paused = error
                    return 1
                return 0

            target.set_progress_handler(sql_progress, 1000)
            try:
                # Full page validation runs on the isolated archive host.
                # Online backup completion, count and hash identify this source cut.
                rows = target.execute("SELECT count(*) FROM archive").fetchone()[0]
            except sqlite3.OperationalError:
                if paused is not None:
                    raise paused from None
                raise
        finally:
            target.close()
            origin.close()
        check()
        finished = datetime.now(UTC).isoformat()
        with output.open("rb") as body:
            os.fsync(body.fileno())
        proof = {
            "schema": 1,
            "environment": "production",
            "complete": True,
            "source_authority": "Biolingo",
            "created_by_uid": os.getuid(),
            "started_at": began,
            "finished_at": finished,
            "rows": rows,
            "bytes": output.stat().st_size,
            "sha256": digest(output),
            "compression_on_source": False,
            "source_integrity_checked": False,
            "archive_integrity_required": True,
            "deleted_files": 0,
        }
        check()
        with (output.parent / "journal-proof.json").open("x") as body:
            os.fchmod(body.fileno(), 0o600)
            json.dump(proof, body)
            body.flush()
            os.fsync(body.fileno())
        return proof


def sqlite_paths(folder):
    # A WAL reader may create shared-memory bookkeeping when no writer is open.
    # The original database and any existing WAL remain mounted read-only.
    return {
        "ReadWritePaths": f"{folder} {COORD} {SOURCE.parent}",
        "ReadOnlyPaths": f"{SOURCE} -{SOURCE}-wal",
    }


def main():
    assert os.uname().nodename == "Biolingo"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path)
    args = parser.parse_args()
    account = pwd.getpwnam("greenmind-raw-copy")
    if args.worker:
        assert os.geteuid() == account.pw_uid
        assert (
            args.worker.parent.parent == STATE
            and args.worker.name == "source-ledger.sqlite3"
        )
        assert args.worker.parent.resolve() == args.worker.parent
        assert args.worker.parent.stat().st_uid == os.getuid()
        assert not args.worker.parent.stat().st_mode & 0o077
        assert all(os.environ.get(k) == "false" for k in FLAGS)
        print(json.dumps(checkpoint(args.worker)))
        return 0
    assert os.geteuid() == 0
    runtime = Path(__file__).resolve().parents[2]
    for name, sha in json.loads((runtime / "files.json").read_text()).items():
        assert digest(runtime / name) == sha
    # A smaller REAL hard cap frees headroom without changing the 640 MiB reserve.
    if available() < 672:
        print(
            json.dumps(
                {
                    "status": "paused",
                    "reason": "source_startup_headroom",
                    "available_mib": round(available(), 1),
                    "required_mib": 672,
                    "started": False,
                    "deleted_files": 0,
                }
            )
        )
        return 75
    guard()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    folder = STATE / ("checkpoint-" + stamp)
    folder.mkdir(mode=0o700)
    os.chown(folder, account.pw_uid, account.pw_gid)
    unit = "greenmind-journal-checkpoint-" + stamp.lower()
    props = {
        "User": account.pw_name,
        "Group": account.pw_name,
        "MemoryMax": "32M",
        "MemorySwapMax": "0",
        "CPUQuota": "10%",
        "Nice": "19",
        "IOSchedulingClass": "idle",
        "TasksMax": "16",
        "Type": "oneshot",
        "TimeoutStartSec": "120",
        "SuccessExitStatus": "75",
        "NoNewPrivileges": "true",
        "PrivateTmp": "true",
        "ProtectSystem": "strict",
        "ProtectHome": "true",
        "UMask": "0077",
        **sqlite_paths(folder),
    }
    cmd = ["systemd-run", "--no-block", "--unit=" + unit]
    cmd += ["--property=" + k + "=" + v for k, v in props.items()]
    cmd += ["--setenv=" + k + "=false" for k in FLAGS]
    cmd += [
        "/usr/bin/python3",
        str(Path(__file__).resolve()),
        "--worker",
        str(folder / "source-ledger.sqlite3"),
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=10)
    print(json.dumps({"unit": unit, "state": str(folder), "deleted_files": 0}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Pause as error:
        print(
            json.dumps({"status": "paused", "reason": str(error), "deleted_files": 0})
        )
        raise SystemExit(75) from None
    except Exception as error:  # noqa: BLE001 - preserve bounded redacted output
        details = {}
        if isinstance(error, sqlite3.Error):
            details = {
                "sqlite_errorcode": getattr(error, "sqlite_errorcode", None),
                "sqlite_errorname": getattr(error, "sqlite_errorname", None),
            }
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error_type": type(error).__name__,
                    "deleted_files": 0,
                    **details,
                }
            )
        )
        raise SystemExit(1) from None
