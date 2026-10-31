"""One copy-only archive-host run, under the unchanged source-authority lease."""

import json
import os
import pwd
import re
from pathlib import Path

from . import runner
from .failure_diagnostics import failure_diagnostic, failure_reason
from .health import SafetyPause
from .policy import ArchiveBlocked
from .remote_lease import bound_source_lease, catalog_lease, source_checkpoint

OWNER = Path("/etc/greenmind/archive-copy-owner.json")


def validate_owner(value):
    if (
        not isinstance(value, dict)
        or value.get("schema") != 1
        or value.get("owner") != "archive"
        or value.get("accepted") is not True
        or value.get("environment") != "production"
        or not isinstance(value.get("cohort"), str)
        or not re.fullmatch("[a-f0-9]{24}", value["cohort"])
        or not isinstance(value.get("source_journal_sha256"), str)
        or not re.fullmatch("[a-f0-9]{64}", value["source_journal_sha256"])
    ):
        raise ArchiveBlocked("Accepted fixed single-writer cohort required")
    return value


def guard():
    if (
        os.uname().nodename != "ubuntu-4gb-fsn1-1"
        or os.getuid() != pwd.getpwnam("greenmind-archive-copy").pw_uid
        or os.environ.get("RAW_ARCHIVE_WORKER_ROLE") != "archive"
        or os.environ.get("RAW_ARCHIVE_ENVIRONMENT") != "production"
        or os.environ.get("RAW_ARCHIVE_ENABLED") != "true"
        or any(
            os.environ.get(key) != "false"
            for key in (
                "RAW_ARCHIVE_DELETE_ENABLED",
                "RAW_ARCHIVE_LEGACY_NULL_ACCEPTED",
                "RETENTION_ENABLED",
                "DIRECT_RETENTION_ENABLED",
                "VISUAL_PRUNE_ENABLED",
            )
        )
        or not 1 <= int(os.environ.get("RAW_ARCHIVE_MAX_SECONDS", "999")) <= 75
        or int(os.environ.get("RAW_ARCHIVE_SOURCE_MIN_MEMORY_MIB", "0")) < 640
        or int(os.environ.get("RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB", "0")) < 800
        or float(os.environ.get("RAW_ARCHIVE_SOURCE_MAX_LOAD", "99")) > 2.4
    ):
        raise ArchiveBlocked("Explicit bounded copy-only archive configuration required")
    for path in (OWNER.parent.parent, OWNER.parent, OWNER):
        if path.is_symlink() or path.stat().st_uid != 0 or path.stat().st_mode & 0o022:
            raise ArchiveBlocked("Root-pinned single-writer ownership required")
    if OWNER.stat().st_size > 8192:
        raise ArchiveBlocked("Ownership evidence exceeds bound")
    return validate_owner(json.loads(OWNER.read_bytes()))


class LeasedProbe:
    def __init__(self, probe, check):
        self.probe, self.check = probe, check

    @property
    def last(self):
        return self.probe.last

    def __call__(self):
        self.check()
        return self.probe()

    def close(self):
        self.probe.close()


def run():
    guard()  # Before locks, SQL, source keys or remote upload access.
    mode = os.getenv("RAW_ARCHIVE_COPY_MODE", "legacy")
    if mode not in {"legacy", "tiered-copy-only"}:
        raise ArchiveBlocked("Explicit supported copy mode required")
    if mode == "tiered-copy-only" and (
        os.getenv("TIERED_ARCHIVE_ENABLED") != "true"
        or os.getenv("TIERED_ARCHIVE_ENVIRONMENT") != "production"
        or os.getenv("TIERED_ARCHIVE_DELETE_ENABLED") != "false"
    ):
        raise ArchiveBlocked("Explicit tiered production copy-only configuration required")
    os.environ["RAW_ARCHIVE_REUSE_SSH"] = "true"
    with catalog_lease() as check, bound_source_lease(check, copy_only=True):
        # Existing catalog transport keeps its immutable 120-second authority.
        if mode == "legacy":
            return runner.run(
                health_factory=lambda *args, **kwargs: LeasedProbe(
                    runner.health_probe(*args, **kwargs), check
                )
            )
        from app.tiered_archive import runtime
        from app.tiered_archive.execution import bounded_job
        from app.tiered_archive.schedule import tick

        source_checkpoint()
        seconds = int(os.environ["RAW_ARCHIVE_MAX_SECONDS"])
        schedule = Path(os.environ["TIERED_ARCHIVE_SCHEDULE_FILE"])
        if not schedule.is_absolute():
            raise ArchiveBlocked("Absolute tiered schedule required")
        with bounded_job(runtime.state_root(), seconds=seconds, emit=False):
            result = tick(schedule, seconds=seconds)
        if result.get("evicted", 0):
            raise ArchiveBlocked("Copy-only schedule attempted eviction")
        return {
            **result,
            "status": "incomplete" if result.get("blocked", 0) else "complete",
            "deleted": 0,
        }


def main():
    try:
        result = run()
    except Exception as error:
        result = {
            "status": "paused" if isinstance(error, SafetyPause) else "blocked",
            "reason": failure_reason(error),
            "deleted": 0,
            "diagnostic": failure_diagnostic(error),
        }
    print(json.dumps(result))
    if result.get("status") in {"complete", "disabled"}:
        return 0
    if (
        result.get("pause_code")
        or result.get("status") == "paused"
        or (
            result.get("status") == "incomplete"
            and not result.get("failed")
            and not result.get("errors")
        )
    ):
        return 75
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
