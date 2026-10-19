"""Read only a root-owned short-lived export lease, never accept stale readiness."""

import json
import os
import re
import sys
import time
from pathlib import Path


def main():
    session = sys.argv[1]
    assert re.fullmatch("[a-f0-9]{24}", session)
    path = Path("/home/traver/greenmind-archive-acceptance-20261007/session-" + session + "/lease.json")
    info = path.stat()
    assert not path.is_symlink() and info.st_uid == 0 and not info.st_mode & 0o022
    data = json.loads(path.read_text())
    now = time.time()
    ready = data.get("ready") is True and data["session"] == session and now < data["deadline"] and 0 <= now - data["heartbeat"] <= 6
    print(json.dumps({"ready": ready}))
    return 0 if ready else 75


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print(json.dumps({"ready": False}))
        raise SystemExit(75)
