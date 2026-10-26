"""Finite daily feature attempts; keep evidence and never prune backups."""

import argparse
import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

STATE = Path(
    os.environ.get(
        "RAW_ARCHIVE_FEATURE_STATE", "/mnt/HC_Volume_106755700/greenmind-feature-backup"
    )
)
PYTHON = os.environ.get(
    "RAW_ARCHIVE_PYTHON", "/opt/greenmind/raw-copy/20260925/venv/bin/python"
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manual-validation", action="store_true")
    args = parser.parse_args()
    if not STATE.is_absolute() or STATE.is_symlink() or not Path(PYTHON).is_absolute():
        raise RuntimeError("Explicit isolated feature runtime and state required")
    day = datetime.now(ZoneInfo("Europe/Zurich")).strftime("%Y-%m-%d")
    folder = STATE / day
    folder.mkdir(mode=0o700, exist_ok=True)
    if not args.manual_validation and any(folder.glob("*/verified.json")):
        print(json.dumps({"status": "already_verified_today", "deleted_files": 0}))
        return 0
    attempts = sorted(folder.glob("attempt-*"))
    if not args.manual_validation and len(attempts) >= 4:
        print(
            json.dumps(
                {
                    "status": "paused",
                    "reason": "daily_attempt_limit",
                    "deleted_files": 0,
                }
            )
        )
        return 75
    # Bound retained failed/verified spools; human reconciliation precedes cleanup.
    if sum(p.stat().st_size for p in STATE.rglob("*") if p.is_file()) > 2 * 1024**3:
        print(
            json.dumps(
                {
                    "status": "paused",
                    "reason": "retained_spool_limit",
                    "deleted_files": 0,
                }
            )
        )
        return 75
    output = (STATE if args.manual_validation else folder) / (
        ("manual-validation-" if args.manual_validation else "attempt-")
        + datetime.now().strftime("%Y%m%dT%H%M%S%f")
    )
    output.mkdir(mode=0o700)
    return run_attempt(output)


def run_attempt(output):
    """Catch the existing copier's yield window; never wait with a SQL connection."""
    for number in range(4):
        result = subprocess.run(
            [
                PYTHON,
                "-m",
                "app.raw_archive.feature_backup",
                "--output",
                str(output),
            ],
            capture_output=True,
            text=True,
            timeout=265,
            env=os.environ.copy(),
        )
        print(result.stdout.strip(), flush=True)
        (output / (f"try-{number}.json")).write_text(result.stdout)
        try:
            value = json.loads(result.stdout)
        except ValueError:
            break
        if value.get("status") != "paused" or value.get("reason") != "archive_job_busy":
            break
        if number < 3:
            time.sleep(20)
    # The entrypoint redacts driver exceptions; do not expose subprocess stderr.
    print(result.stdout.strip())
    (output / "attempt-result.json").write_text(result.stdout)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
