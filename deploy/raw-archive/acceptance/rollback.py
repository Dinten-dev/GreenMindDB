"""Revert only this release's job overrides; retain files and evidence."""

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

from config_files import withdraw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    assert os.geteuid() == 0 and not args.manifest.is_symlink()
    data = json.loads(args.manifest.read_text())
    state = Path(data["state"])
    assert state == args.manifest.parent
    for unit, item in data["overrides"].items():
        assert unit in {
            "greenmind-raw-copy.service",
            "greenmind-archive-export.service",
        }
        path = Path(item["path"])
        assert path.parent == Path("/etc/systemd/system") / (unit + ".d")
        assert (
            not path.is_symlink()
            and hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
        )
    previous = data.get("previous_overrides", {})
    for unit, item in previous.items():
        assert (
            unit in data["overrides"]
            and item["original_path"] == data["overrides"][unit]["path"]
        )
        path = Path(item["backup_path"])
        assert path.parent == state and not path.is_symlink()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
        inactive = Path(item["rollback_path"])
        assert (
            inactive.parent == Path(item["original_path"]).parent
            and not inactive.is_symlink()
        )
        assert hashlib.sha256(inactive.read_bytes()).hexdigest() == item["sha256"]
    for item in data["overrides"].values():
        path = Path(item["path"])
        withdraw(path, state / (path.name + ".reverted"), item["sha256"])
    for item in previous.values():
        Path(item["rollback_path"]).rename(item["original_path"])
    subprocess.run(["systemctl", "daemon-reload"], check=True, timeout=10)
    print(
        json.dumps(
            {
                "status": "overrides_reverted",
                "running_jobs_untouched": True,
                "deleted_files": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
