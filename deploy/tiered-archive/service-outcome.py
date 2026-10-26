"""systemd ExecStopPost: hard failures persist a pause and local metrics."""

import argparse
import json
import os
import time
from pathlib import Path


def record(root, kind, result, exit_status, pause_dir=None):
    root = Path(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    pause_dir = Path(pause_dir or root)
    peak = None
    oom_kills = None
    try:
        cgroup = next(
            line.split(":", 2)[2]
            for line in Path("/proc/self/cgroup").read_text().splitlines()
            if line.startswith("0::")
        )
        directory = Path("/sys/fs/cgroup") / cgroup.lstrip("/")
        peak = int((directory / "memory.peak").read_text())
        oom_kills = dict(
            line.split()
            for line in (directory / "memory.events").read_text().splitlines()
        ).get("oom_kill")
    except (OSError, ValueError, StopIteration):
        pass
    failed = (
        result != "success"
        or peak is not None
        and peak > 160 * 1024**2
        or int(oom_kills or 0) > 0
    )
    if failed:
        with (pause_dir / "PAUSE").open("a") as body:
            body.write(
                json.dumps(
                    {
                        "at": time.time(),
                        "kind": kind,
                        "result": result,
                        "exit_status": exit_status,
                    }
                )
                + "\n"
            )
            body.flush()
            os.fsync(body.fileno())
        os.chmod(pause_dir / "PAUSE", 0o640)
    value = {
        "schema": 1,
        "at": time.time(),
        "kind": kind,
        "result": result,
        "exit_status": exit_status,
        "paused": (pause_dir / "PAUSE").exists(),
        "cgroup_memory_peak_bytes": peak,
        "cgroup_oom_kills": oom_kills,
    }
    for suffix, content in [
        ("json", json.dumps(value, sort_keys=True) + "\n"),
        (
            "prom",
            (
                f'greenmind_tiered_staging_last_stop_seconds{{job="{kind}"}} {value["at"]}\n'
                f'greenmind_tiered_staging_job_failed{{job="{kind}"}} {int(failed)}\n'
                f'greenmind_tiered_staging_paused{{job="{kind}"}} {int(value["paused"])}\n'
            ),
        ),
    ]:
        target = root / (kind + "-service-result." + suffix)
        temporary = target.with_suffix(".pending")
        with temporary.open("w") as out:
            os.chmod(temporary, 0o600)
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, target)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument(
        "--kind",
        choices=["copy", "backup-gateway", "backup-direct", "reader"],
        required=True,
    )
    parser.add_argument("--pause-dir", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            record(
                args.state,
                args.kind,
                os.environ.get("SERVICE_RESULT", "unknown"),
                os.environ.get("EXIT_STATUS", "unknown"),
                args.pause_dir,
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
