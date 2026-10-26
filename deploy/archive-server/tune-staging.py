"""Correct measured soft throttling; preserve the 1024 MiB staging hard limit."""

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

ORIGINAL = b"""[Unit]
Description=GreenMind staging capacity budget
[Slice]
MemoryHigh=768M
MemoryMax=1024M
MemorySwapMax=0
CPUQuota=50%
TasksMax=256
"""


def main():
    assert os.geteuid() == 0 and os.uname().nodename == "ubuntu-4gb-fsn1-1"
    target = Path("/etc/systemd/system/greenmind-staging.slice")
    current = target.read_bytes()
    expected = ORIGINAL.replace(b"MemoryHigh=768M", b"MemoryHigh=896M").replace(
        b"CPUQuota=50%", b"CPUQuota=75%"
    )
    assert current == ORIGINAL, "Concurrent staging budget change"
    assert (
        subprocess.check_output(
            ["systemctl", "show", "greenmind.slice", "-p", "MemoryMax", "--value"],
            text=True,
        ).strip()
        == "2684354560"
    )
    assert (
        subprocess.check_output(
            [
                "systemctl",
                "show",
                "greenmind-staging.slice",
                "-p",
                "MemoryMax",
                "--value",
            ],
            text=True,
        ).strip()
        == "1073741824"
    )
    evidence = Path("/var/lib/greenmind/evidence/staging-budget-20261008-v4.json")
    assert not evidence.exists()
    with evidence.open("x") as body:
        os.fchmod(body.fileno(), 0o600)
        json.dump(
            {
                "before": current.decode(),
                "before_sha256": hashlib.sha256(current).hexdigest(),
                "after_sha256": hashlib.sha256(expected).hexdigest(),
                "hard_limit_unchanged": True,
            },
            body,
        )
    fd, name = tempfile.mkstemp(prefix=".staging-budget-", dir=target.parent)
    with os.fdopen(fd, "wb") as body:
        os.fchmod(body.fileno(), 0o644)
        body.write(expected)
        body.flush()
        os.fsync(body.fileno())
    os.replace(name, target)
    subprocess.run(["systemctl", "daemon-reload"], check=True, timeout=15)
    subprocess.run(
        [
            "systemctl",
            "set-property",
            "--runtime",
            "greenmind-staging.slice",
            "MemoryHigh=896M",
            "CPUQuota=75%",
        ],
        check=True,
        timeout=15,
    )
    print(
        json.dumps(
            {
                "staging_soft_mib": 896,
                "staging_hard_mib": 1024,
                "parent_hard_mib": 2560,
                "deletion_enabled": False,
            }
        )
    )


if __name__ == "__main__":
    main()
