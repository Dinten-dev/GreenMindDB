"""Hold only the archive lease for a bounded Mac export; receivers keep running."""

import json
import os
import pwd
import secrets
import time
import urllib.request
from pathlib import Path

from app.raw_archive.coordination import lease
from app.raw_archive.health import SafetyPause
from app.raw_archive.recovery import backup_ledger

ROOT = Path("/home/traver/greenmind-archive-acceptance-20261007")


def healthy():
    memory = (
        next(
            int(line.split()[1])
            for line in Path("/proc/meminfo").read_text().splitlines()
            if line.startswith("MemAvailable:")
        )
        / 1024
    )
    load = os.getloadavg()[0]
    if memory < 640:
        raise SafetyPause(
            "memory", {"available_mib": round(memory, 1), "required_mib": 640}
        )
    if load > 2.4:
        raise SafetyPause("host_load", {"load": round(load, 3), "maximum": 2.4})
    for port in (8120, 8003, 8000):
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/health", timeout=2
        ) as response:
            assert response.status == 200


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--operation", choices=("legacy", "backup", "sql"), default="legacy"
    )
    args = parser.parse_args()
    assert os.geteuid() == 0
    os.environ["RAW_ARCHIVE_COORDINATION_DIR"] = (
        "/var/lib/greenmind-archive-coordination"
    )
    try:
        with lease("catalog"):
            healthy()
            operator = pwd.getpwnam("traver")
            session = secrets.token_hex(12)
            folder = ROOT / ("session-" + session)
            folder.mkdir(mode=0o750)
            os.chown(folder, 0, operator.pw_gid)
            backup = folder / "ledger.sqlite3"
            if args.operation != "sql":
                backup_ledger(
                    Path("/var/lib/greenmind-raw-copy/archive.sqlite3"),
                    backup,
                    checkpoint=healthy,
                )
                backup.chmod(0o640)
                os.chown(backup, 0, operator.pw_gid)
            if args.operation == "backup":
                print(
                    json.dumps(
                        {
                            "status": "backed_up",
                            "ledger": str(backup),
                            "deleted_files": 0,
                        }
                    )
                )
                return 0
            until = time.time() + 120
            result = {
                "session": session,
                "pid": os.getpid(),
                "ledger": str(backup) if args.operation != "sql" else None,
                "deadline": until,
                "ready": True,
                "operation": args.operation,
            }
            state = folder / "lease.json"

            def publish():
                result["heartbeat"] = time.time()
                state.write_text(json.dumps(result))
                os.chown(state, 0, operator.pw_gid)
                state.chmod(0o640)

            publish()
            print(json.dumps(result), flush=True)
            try:
                while time.time() < until:
                    healthy()
                    publish()
                    time.sleep(2)
            finally:
                # Withdraw readiness before unlocking, including resource pauses.
                result["ready"] = False
                publish()
            return 0
    except SafetyPause as error:
        print(
            json.dumps(
                {
                    "status": "paused",
                    "reason": error.code,
                    "details": error.details,
                    "deleted_files": 0,
                }
            ),
            flush=True,
        )
        return 75


if __name__ == "__main__":
    raise SystemExit(main())
