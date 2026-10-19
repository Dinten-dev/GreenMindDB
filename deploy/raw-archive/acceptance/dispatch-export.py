"""Skip empty export queues before Docker/API dependencies allocate memory."""

import argparse
import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path

from app.raw_archive.coordination import lease
from app.raw_archive.health import SafetyPause


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--compose", type=Path, required=True)
    args = parser.parse_args()
    source = args.state / "jobs.sqlite3"
    if source.is_symlink() or not args.state.is_absolute():
        raise RuntimeError("Private export state required")
    if not source.exists():
        print(json.dumps({"status": "idle"}))
        return 0
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=2) as db:
        db.execute("PRAGMA query_only=ON")
        queued = db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','working') AND created>=? LIMIT 1", (time.time() - 86400,)).fetchone()
    if not queued:
        print(json.dumps({"status": "idle"}))
        return 0
    try:
        with lease("export"):
            return subprocess.run(["/usr/bin/docker", "-H", "unix:///var/run/docker.sock", "compose", "-f", str(args.compose), "--profile", "exports", "run", "--rm", "-T", "export-worker"], timeout=4500).returncode
    except SafetyPause as error:
        print(json.dumps({"status": "paused", "reason": error.code, "deleted_files": 0}))
        return 75


if __name__ == "__main__":
    raise SystemExit(main())
