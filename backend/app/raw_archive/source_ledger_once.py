"""One source-authoritative, online journal checkpoint; no SQL or policy changes."""

import argparse
import json
import os
import pwd
import time
from datetime import UTC, datetime
from pathlib import Path

from . import catalog_stages, wav_catalog
from .coordination import lease
from .health import SafetyPause
from .policy import ArchiveBlocked
from .recovery import backup_ledger
from .runner import health_probe

SOURCE = Path("/var/lib/greenmind-raw-copy/archive.sqlite3")


def run(output):
    if (
        os.uname().nodename != "Biolingo"
        or os.geteuid() != pwd.getpwnam("greenmind-raw-copy").pw_uid
        or os.environ.get("RAW_ARCHIVE_ENVIRONMENT") != "production"
        or os.environ.get("RAW_ARCHIVE_WORKER_ROLE") == "archive"
        or os.environ.get("RAW_ARCHIVE_COORDINATION_DIR")
        != "/var/lib/greenmind-archive-coordination"
        or output.exists()
    ):
        raise ArchiveBlocked("Fresh private source journal preparation required")
    if any(
        os.environ.get(name) != "false"
        for name in (
            "RAW_ARCHIVE_ENABLED",
            "RAW_ARCHIVE_DELETE_ENABLED",
            "RAW_ARCHIVE_LEGACY_NULL_ACCEPTED",
            "RETENTION_ENABLED",
            "DIRECT_RETENTION_ENABLED",
            "VISUAL_PRUNE_ENABLED",
        )
    ):
        raise ArchiveBlocked("Destructive policies must remain disabled")
    os.environ["RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB"] = str(
        max(640, int(os.environ.get("RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB", "640")))
    )
    os.environ["RAW_ARCHIVE_MAX_HOST_LOAD"] = str(
        min(2.4, float(os.environ.get("RAW_ARCHIVE_MAX_HOST_LOAD", "2.4")))
    )
    probe = health_probe()
    started = time.monotonic()

    def checkpoint():
        if time.monotonic() - started >= 110:
            raise SafetyPause("ledger_phase_deadline")
        if not probe():
            raise SafetyPause(probe.last.get("reason", "headroom"), probe.last)

    try:
        with lease("catalog"):
            checkpoint()
            wav_catalog.private_directory(output)
            began = datetime.now(UTC).isoformat()
            journal = output / "source-ledger.sqlite3"
            proof = backup_ledger(SOURCE, journal, checkpoint=checkpoint)
            finished = datetime.now(UTC).isoformat()
            proof.update(started_at=began, finished_at=finished, created_by_uid=os.geteuid())
            wav_catalog.save_json(output / "journal-proof.json", proof)
            files = wav_catalog.export_ledger(journal, output, checkpoint, compresslevel=1)
            checkpoint()
            if sum(p["decoded_bytes"] for p in files) > 512 * 1024**2:
                raise SafetyPause("ledger_spool_budget")
            stage = catalog_stages.completed_stage(
                "ledger",
                began,
                files,
                finished_at=finished,
                source_sha256=proof["sha256"],
                processed_at=datetime.now(UTC).isoformat(),
            )
            catalog_stages.validate_stage(stage, "ledger", output)
            wav_catalog.save_json(output / "stage.json", stage)
            return {
                "status": "ledger_complete",
                "rows": proof["rows"],
                "complete_catalog": False,
                "deleted_files": 0,
            }
    finally:
        probe.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.output)))
        return 0
    except SafetyPause as error:
        print(json.dumps({"status": "paused", "reason": error.code, "deleted_files": 0}))
        return 75
    except Exception as error:
        print(
            json.dumps(
                {"status": "blocked", "error_type": type(error).__name__, "deleted_files": 0}
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
