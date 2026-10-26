"""One archive-host export with source lease, original reserves and progress."""

import json
import os
import pwd
import time

from app.visualization import archive_exports_worker as worker

from .health import SafetyPause
from .policy import ArchiveBlocked
from .remote_lease import catalog_lease
from .runner import health_probe


def guard():
    if (
        os.uname().nodename != "ubuntu-4gb-fsn1-1"
        or os.getuid() != pwd.getpwnam("greenmind-archive-read").pw_uid
        or os.getenv("RAW_ARCHIVE_WORKER_ROLE") != "archive"
        or os.getenv("RAW_ARCHIVE_ENVIRONMENT") != "production"
        or os.getenv("RAW_ARCHIVE_DELETE_ENABLED") != "false"
        or os.getenv("RAW_ARCHIVE_ENABLED") != "false"
        or os.getenv("ARCHIVE_EXPORTS_ENABLED") != "true"
        or int(os.getenv("RAW_ARCHIVE_SOURCE_MIN_MEMORY_MIB", "0")) < 640
        or int(os.getenv("RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB", "0")) < 800
        or float(os.getenv("RAW_ARCHIVE_SOURCE_MAX_LOAD", "99")) > 2.4
        or any(
            os.getenv(k) != "false"
            for k in ("RETENTION_ENABLED", "DIRECT_RETENTION_ENABLED", "VISUAL_PRUNE_ENABLED")
        )
    ):
        raise ArchiveBlocked("Explicit read-only bounded archive export required")
    if any(k.startswith("RAW_ARCHIVE_SFTP_") for k in os.environ):
        raise ArchiveBlocked("Exports must not receive archive upload access")


def run():
    guard()
    path = worker.root()
    with worker.connect(path) as db:
        pending = db.execute(
            "SELECT 1 FROM jobs WHERE status IN ('queued','working') AND created>=? LIMIT 1",
            (time.time() - worker.EXPIRES,),
        ).fetchone()
    if not pending:
        return {"status": "idle"}
    # Readers and the one copy writer share a root-owned, narrowly ACLed lock.
    from .coordination import lease

    with lease("export"), catalog_lease() as source_check:
        probe = health_probe()
        started = time.monotonic()
        original = worker.headroom

        def check(folder):
            try:
                source_check()
                if time.monotonic() - started >= 75:
                    raise SafetyPause("export_slice_deadline")
                if not probe():
                    raise SafetyPause(probe.last.get("reason", "headroom"))
                # Keep the original disk guard as well; 512 MiB is redundant
                # with the stronger archive-local 800 MiB check above.
                original(folder)
            except SafetyPause as error:
                raise worker.CapacityPause(error.code) from None

        worker.headroom = check
        try:
            return worker._run_once()
        finally:
            worker.headroom = original
            probe.close()


def main():
    try:
        result = run()
    except Exception as error:
        result = {
            "status": "paused" if isinstance(error, SafetyPause) else "blocked",
            "reason": error.code if isinstance(error, SafetyPause) else type(error).__name__,
        }
    print(json.dumps(result))
    if result["status"] in {"ready", "idle"}:
        return 0
    return 75 if result["status"].startswith("paused") else 1


if __name__ == "__main__":
    raise SystemExit(main())
