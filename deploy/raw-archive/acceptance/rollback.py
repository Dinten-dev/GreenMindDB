"""Revert only this release's job overrides; retain files and evidence."""

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    assert os.geteuid() == 0 and not args.manifest.is_symlink()
    data = json.loads(args.manifest.read_text())
    state = Path(data["state"])
    assert state == args.manifest.parent
    for unit, item in data["overrides"].items():
        assert unit in {"greenmind-raw-copy.service", "greenmind-archive-export.service"}
        path = Path(item["path"])
        assert path.parent == Path("/etc/systemd/system") / (unit + ".d")
        assert not path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
    for item in data["overrides"].values():
        path = Path(item["path"])
        path.rename(state / (path.name + ".reverted"))
    subprocess.run(["systemctl", "daemon-reload"], check=True, timeout=10)
    print(json.dumps({"status": "overrides_reverted", "running_jobs_untouched": True, "deleted_files": 0}))


if __name__ == "__main__":
    main()
