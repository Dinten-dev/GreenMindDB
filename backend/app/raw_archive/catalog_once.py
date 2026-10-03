"""Finite metadata-only backup retries; never invokes RAW copy or eviction."""

import argparse
import contextlib
import io
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from .policy import ArchiveBlocked


def main():
    from . import recovery

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    deadline = datetime.fromisoformat(os.environ["RAW_ARCHIVE_PREPARATION_DEADLINE"])
    if deadline.tzinfo is None:
        raise ArchiveBlocked("Explicit timezone-aware preparation deadline required")
    if args.report.exists():
        existing = json.loads(args.report.read_text())
        if existing.get("status") != "backed_up" or not existing.get("offsite_manifest"):
            raise ArchiveBlocked("Existing publication report is invalid")
        print(json.dumps({"status": "already_published", "deleted_files": 0}))
        return 0
    if datetime.now(UTC) >= deadline:
        result = {"status": "expired", "complete": False, "deleted_files": 0}
        expired = args.report.parent / "catalog-expired.json"
        with expired.open("x") as body:
            os.chmod(body.name, 0o600)
            json.dump(result, body)
        print(json.dumps(result))
        return 0
    arguments = sys.argv
    captured = io.StringIO()
    try:
        sys.argv = [
            "recovery",
            "--output",
            str(args.output),
            "--ledger",
            "/var/lib/greenmind-raw-copy/archive.sqlite3",
            "--publish-catalog",
        ]
        with contextlib.redirect_stdout(captured):
            status = recovery.entrypoint()
    finally:
        sys.argv = arguments
    print(captured.getvalue(), end="")
    if status == 0:
        result = json.loads(captured.getvalue())
        if result.get("status") != "backed_up" or not result.get("offsite_manifest"):
            raise ArchiveBlocked("Incomplete catalog publication cannot finish retries")
        with args.report.open("x") as body:
            os.chmod(body.name, 0o600)
            json.dump(result | {"restoration_passed": False, "deleted_files": 0}, body, indent=2)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
