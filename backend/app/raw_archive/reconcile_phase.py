"""One bounded read-only fixed-cohort phase, never enable deletion."""

import argparse
import json
import os
import pwd
from pathlib import Path

from . import catalog_reconcile_resume as scan
from .health import SafetyPause
from .policy import ArchiveBlocked
from .runner import health_probe

BASE = Path("/var/lib/greenmind/archive-reader-validation-20261009")


def path(value):
    result = Path(value)
    if not result.is_absolute() or BASE not in result.parents or result.resolve() != result:
        raise ArchiveBlocked("Explicit private archive verification path required")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("prepare", *scan.PHASES, "finish"), required=True)
    parser.add_argument("--output", type=path, required=True)
    parser.add_argument("--recovery", type=path)
    parser.add_argument("--quarantine", type=path)
    args = parser.parse_args()
    probe = None
    try:
        if (
            os.uname().nodename != "ubuntu-4gb-fsn1-1"
            or os.getuid() != pwd.getpwnam("greenmind-archive-read").pw_uid
            or os.getenv("RAW_ARCHIVE_WORKER_ROLE") != "archive"
            or os.getenv("RAW_ARCHIVE_DELETE_ENABLED") != "false"
            or os.getenv("RAW_ARCHIVE_ENABLED") != "false"
            or os.getenv("RETENTION_ENABLED") != "false"
            or os.getenv("DIRECT_RETENTION_ENABLED") != "false"
            or os.getenv("VISUAL_PRUNE_ENABLED") != "false"
            or int(os.getenv("RAW_ARCHIVE_SOURCE_MIN_MEMORY_MIB", "0")) < 640
            or int(os.getenv("RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB", "0")) < 800
            or any(k.startswith("RAW_ARCHIVE_SFTP_") for k in os.environ)
        ):
            raise ArchiveBlocked("Bounded read-only reconciliation required")
        probe = health_probe()

        def check():
            if not probe():
                raise SafetyPause(probe.last.get("reason", "headroom"))

        check()
        if args.phase == "prepare":
            if args.recovery is None or args.quarantine is None:
                raise ArchiveBlocked("Pinned recovery and quarantine required")
            from .catalog_resume import private

            private(args.quarantine)
            quarantine = json.loads(args.quarantine.read_text())
            if quarantine.get("environment") != "production":
                raise ArchiveBlocked("Production quarantine required")
            excluded = [(row["kind"], row["identity"]) for row in quarantine["excluded"]]
            result = scan.prepare(args.recovery, args.output, excluded=excluded, checkpoint=check)
        elif args.phase == "finish":
            result = scan.finish(args.output, checkpoint=check)
        else:
            result = scan.phase(args.output, args.phase, checkpoint=check)
        print(json.dumps(result))
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "paused" if isinstance(error, SafetyPause) else "blocked",
                    "reason": error.code
                    if isinstance(error, SafetyPause)
                    else type(error).__name__,
                    "deleted_files": 0,
                }
            )
        )
        return 75 if isinstance(error, SafetyPause) else 1
    finally:
        if probe is not None:
            probe.close()


if __name__ == "__main__":
    raise SystemExit(main())
