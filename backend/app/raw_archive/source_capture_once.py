"""Bounded local capture, avoiding network transfer inside the SQL snapshot.

The separate archive host validates the completed capture afterward. No upload
credentials, schedule changes, wider source grants or receiver modifications.
"""

import argparse
import json
import os
import pwd
import time
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from .catalog_capture import capture
from .coordination import lease
from .health import SafetyPause
from .policy import ArchiveBlocked
from .runner import health_probe, metadata_source_settings


def run(output):
    if (
        os.uname().nodename != "Biolingo"
        or os.geteuid() != pwd.getpwnam("greenmind-raw-copy").pw_uid
        or os.getenv("RAW_ARCHIVE_ENVIRONMENT") != "production"
        or os.getenv("RAW_ARCHIVE_WORKER_ROLE") == "archive"
        or os.getenv("RAW_ARCHIVE_SOURCE_PROBE_URL")
        or os.getenv("RAW_ARCHIVE_COORDINATION_DIR") != "/var/lib/greenmind-archive-coordination"
    ):
        raise ArchiveBlocked("Exact private source identity and local lease required")
    if any(
        os.getenv(name) != "false"
        for name in (
            "RAW_ARCHIVE_ENABLED",
            "RAW_ARCHIVE_DELETE_ENABLED",
            "RETENTION_ENABLED",
            "DIRECT_RETENTION_ENABLED",
            "VISUAL_PRUNE_ENABLED",
        )
    ):
        raise ArchiveBlocked("Destructive policies must remain disabled")
    if output.exists():
        raise ArchiveBlocked("Retain previous attempts; require new output")
    os.environ["RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB"] = str(
        max(640, int(os.getenv("RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB", "640")))
    )
    os.environ["RAW_ARCHIVE_MAX_HOST_LOAD"] = str(
        min(2.4, float(os.getenv("RAW_ARCHIVE_MAX_HOST_LOAD", "2.4")))
    )
    settings = metadata_source_settings("gateway", "production")
    url = make_url(settings["database"])
    if url.host != "127.0.0.1" or url.username != "greenmind_wav_metadata_b4ae52d674aa":
        raise ArchiveBlocked("Existing column-limited local SQL reader required")
    probe = health_probe()
    started = time.monotonic()

    def checkpoint():
        if time.monotonic() - started >= 110:
            raise SafetyPause("catalog_phase_deadline")
        if not probe():
            raise SafetyPause(probe.last.get("reason", "headroom"), probe.last)

    try:
        with lease("catalog"):
            checkpoint()
            engine = create_engine(
                settings["database"],
                pool_size=1,
                max_overflow=0,
                connect_args={
                    "connect_timeout": 5,
                    "options": "-c default_transaction_read_only=on -c statement_timeout=8000 "
                    "-c lock_timeout=150 -c jit=off -c work_mem=2048",
                },
            )
            try:
                proof = capture(engine, "gateway", output, checkpoint)
            finally:
                engine.dispose()
            return {
                "status": "captured",
                "kind": "gateway",
                "source_read_only": True,
                "tables": {entry["table"]: entry["rows"] for entry in proof["files"]},
                "decoded_validation_complete": False,
                "deleted_files": 0,
            }
    finally:
        probe.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.output)))
    except Exception as error:  # noqa: BLE001 - redact SQL credentials
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
        raise SystemExit(75 if isinstance(error, SafetyPause) else 1) from None
