"""Measured staging needs a larger share; the host's 2560 MiB cap is fixed."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

BEFORE = {
    "greenmind-archive.slice": """[Unit]
Description=GreenMind archive capacity budget
[Slice]
MemoryHigh=1280M
MemoryMax=1536M
MemorySwapMax=0
CPUQuota=100%
TasksMax=256
""",
    "greenmind-staging.slice": """[Unit]
Description=GreenMind staging capacity budget
[Slice]
MemoryHigh=896M
MemoryMax=1024M
MemorySwapMax=0
CPUQuota=75%
TasksMax=256
""",
}


def main():
    assert os.geteuid() == 0 and os.uname().nodename == "ubuntu-4gb-fsn1-1"
    system = Path("/etc/systemd/system")
    assert (
        subprocess.check_output(
            ["systemctl", "show", "greenmind.slice", "-p", "MemoryMax", "--value"],
            text=True,
        ).strip()
        == "2684354560"
    )
    assert (
        int(
            subprocess.check_output(
                [
                    "systemctl",
                    "show",
                    "greenmind-archive.slice",
                    "-p",
                    "MemoryCurrent",
                    "--value",
                ],
                text=True,
            )
        )
        < 128 * 1024**2
    )
    for name, value in BEFORE.items():
        assert (system / name).read_text() == value, "Concurrent capacity change"
    after = {name: Path(__file__).with_name(name).read_text() for name in BEFORE}
    assert after["greenmind-staging.slice"] == BEFORE[
        "greenmind-staging.slice"
    ].replace("MemoryHigh=896M", "MemoryHigh=1280M").replace(
        "MemoryMax=1024M", "MemoryMax=1536M"
    ).replace("CPUQuota=75%", "CPUQuota=100%")
    assert after["greenmind-archive.slice"] == BEFORE[
        "greenmind-archive.slice"
    ].replace("MemoryHigh=1280M", "MemoryHigh=768M").replace(
        "MemoryMax=1536M", "MemoryMax=1024M"
    ).replace("CPUQuota=100%", "CPUQuota=50%")
    proof = Path("/var/lib/greenmind/evidence/capacity-rebalance-20261008-v5.json")
    with proof.open("x") as body:
        os.fchmod(body.fileno(), 0o600)
        json.dump(
            {
                "before": BEFORE,
                "after": after,
                "parent_hard_mib": 2560,
                "source_reserves_unchanged": True,
            },
            body,
        )
    # Lower the idle archive share first, then raise staging. Total stays bounded.
    for name, high, hard, cpu in [
        ("greenmind-archive.slice", 768, 1024, 50),
        ("greenmind-staging.slice", 1280, 1536, 100),
    ]:
        fd, temporary = tempfile.mkstemp(prefix=".greenmind-budget-", dir=system)
        with os.fdopen(fd, "w") as body:
            os.fchmod(body.fileno(), 0o644)
            body.write(after[name])
            body.flush()
            os.fsync(body.fileno())
        os.replace(temporary, system / name)
        subprocess.run(["systemctl", "daemon-reload"], check=True, timeout=15)
        subprocess.run(
            [
                "systemctl",
                "set-property",
                "--runtime",
                name,
                f"MemoryHigh={high}M",
                f"MemoryMax={hard}M",
                f"CPUQuota={cpu}%",
            ],
            check=True,
            timeout=15,
        )
    print(
        json.dumps(
            {
                "staging_hard_mib": 1536,
                "archive_hard_mib": 1024,
                "parent_hard_mib": 2560,
                "deleted_files": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
