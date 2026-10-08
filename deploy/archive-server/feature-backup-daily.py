"""Finite daily feature attempts; keep evidence and never prune backups."""

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

STATE = Path("/mnt/HC_Volume_106755700/greenmind-feature-backup")


def main():
    day = datetime.now(ZoneInfo("Europe/Zurich")).strftime("%Y-%m-%d")
    folder = STATE / day
    folder.mkdir(mode=0o700, exist_ok=True)
    if any(folder.glob("*/verified.json")):
        print(json.dumps({"status": "already_verified_today", "deleted_files": 0}))
        return 0
    attempts = sorted(folder.glob("attempt-*"))
    if len(attempts) >= 4:
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
    output = folder / ("attempt-" + datetime.now().strftime("%H%M%S%f"))
    output.mkdir(mode=0o700)
    result = subprocess.run(
        [
            "/opt/greenmind/raw-copy/20260925/venv/bin/python",
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
    # The entrypoint redacts driver exceptions; do not expose subprocess stderr.
    print(result.stdout.strip())
    (output / "attempt-result.json").write_text(result.stdout)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
