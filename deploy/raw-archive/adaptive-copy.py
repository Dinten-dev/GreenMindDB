"""Copy-only launcher with RAM-scaled batch size and a protected RAM reserve."""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

# Packaged releases import only their own code, including SFTP subprocesses.
release_backend = Path(__file__).resolve().parent / "backend"
if release_backend.is_dir():
    sys.path.insert(0, str(release_backend))
    os.environ["PYTHONPATH"] = str(release_backend)

from app.raw_archive.policy import ArchiveBlocked  # noqa: E402 - packaged import path first
from app.raw_archive.health import SafetyPause  # noqa: E402
from app.raw_archive.runner import main  # noqa: E402


def plan(
    available_kib: int,
    cpu_count: int,
    configured_bytes: int,
    minimum_memory_mib: int = 512,
    maximum_host_load: float = 2.4,
) -> dict[str, int | float]:
    if available_kib <= 0 or cpu_count <= 0 or configured_bytes <= 0:
        raise ArchiveBlocked("Invalid adaptive resource inputs")
    available_bytes = available_kib * 1024
    if minimum_memory_mib < 512 or not 0 < maximum_host_load <= 2.4:
        raise ArchiveBlocked("Configured protection must not be weakened")
    reserve_bytes = max(minimum_memory_mib * 1024**2, math.ceil(available_bytes * 0.20))
    transfer_bytes = (
        configured_bytes  # Streaming volume is independent of RAM allocation.
    )
    if available_bytes <= reserve_bytes or transfer_bytes < 1024**2:
        raise SafetyPause(
            "memory",
            {
                "available_mib": available_bytes / 1024**2,
                "required_mib": math.ceil(reserve_bytes / 1024**2),
            },
        )
    return {
        "available_kib": available_kib,
        "reserve_mib": math.ceil(reserve_bytes / 1024**2),
        "copy_budget_bytes": transfer_bytes,
        "maximum_host_load": min(maximum_host_load, max(0.5, cpu_count * 0.8)),
    }


def run():
    memory = {
        line.split(":", 1)[0]: int(line.split()[1])
        for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith(("MemAvailable:", "MemTotal:"))
    }
    configured = int(os.environ.get("RAW_ARCHIVE_MAX_BYTES", str(1024**3)))
    limits = plan(
        memory.get("MemAvailable", 0),
        len(os.sched_getaffinity(0)),
        configured,
        max(512, int(os.environ.get("RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB", "512"))),
        min(2.4, float(os.environ.get("RAW_ARCHIVE_MAX_HOST_LOAD", "2.4"))),
    )

    # This standalone launcher cannot acquire deletion permission through env changes.
    for name in (
        "RAW_ARCHIVE_DELETE_ENABLED",
        "RAW_ARCHIVE_READS_ACCEPTED",
        "RAW_ARCHIVE_READS_ENABLED",
        "RETENTION_ENABLED",
        "DIRECT_RETENTION_ENABLED",
    ):
        os.environ[name] = "false"
    os.environ["RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB"] = str(limits["reserve_mib"])
    os.environ["RAW_ARCHIVE_MAX_HOST_LOAD"] = str(limits["maximum_host_load"])
    os.environ["RAW_ARCHIVE_MAX_BYTES"] = str(limits["copy_budget_bytes"])
    print(json.dumps({"adaptive_copy_plan": limits}, sort_keys=True), flush=True)
    return main()


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except SafetyPause as error:
        print(
            json.dumps(
                {
                    "status": "paused",
                    "pause_code": error.code,
                    "guard": error.details,
                    "deleted_files": 0,
                }
            ),
            flush=True,
        )
        raise SystemExit(75)
