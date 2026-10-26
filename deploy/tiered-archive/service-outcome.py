"""ExecStopPost reads remaining group counters and the in-flight sampler proof."""

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.tiered_archive.resources import pause, read_cgroup, reasons, write_json


def record(root, kind, result, exit_status, pause_dir=None):
    root = Path(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    pause_dir = Path(pause_dir or root)
    sample = read_cgroup()
    causes = [] if result == "success" else ["service_" + result]
    proof = None
    required = os.environ.get("TIERED_ARCHIVE_CGROUP_MONITOR") == "true"
    try:
        proof = json.loads((root / (kind + "-resources.json")).read_text())
        if proof.get("invocation_id") != os.environ.get("INVOCATION_ID"):
            proof = None
    except (OSError, ValueError):
        pass
    if sample.get("available") or required:
        causes += reasons(sample)
    if required:
        if not proof or not proof.get("completed") or proof.get("samples", 0) < 2:
            causes.append("inflight_measurement_incomplete")
        elif proof.get("sample_failures"):
            causes.append("inflight_measurement_failed")
    if proof:
        causes += proof.get("reasons", [])
    if causes:
        pause(pause_dir, kind, sorted(set(causes)))
    value = {
        "schema": 2,
        "at": time.time(),
        "kind": kind,
        "result": result,
        "exit_status": exit_status,
        "reasons": sorted(set(causes)),
        "paused": (pause_dir / "PAUSE").exists(),
        "cgroup_metrics_available": sample.get("available", False),
        "cgroup_memory_peak_bytes": sample.get("peak_bytes"),
        "cgroup_events": sample.get("events"),
        "cgroup_anon_bytes": sample.get("anon_bytes"),
        "sampled_anon_peak_bytes": proof.get("sampled_anon_peak_bytes")
        if proof
        else None,
        "inflight_measurement_complete": bool(proof and proof.get("completed")),
    }
    write_json(root / (kind + "-service-result.json"), value)
    metrics = [
        f'greenmind_tiered_staging_last_stop_seconds{{job="{kind}"}} {value["at"]}',
        f'greenmind_tiered_staging_job_failed{{job="{kind}"}} {int(bool(causes))}',
        f'greenmind_tiered_staging_paused{{job="{kind}"}} {int(value["paused"])}',
    ]
    for event in ("high", "max", "oom", "oom_kill"):
        count = sample.get("events", {}).get(event, "NaN")
        metrics.append(
            f'greenmind_tiered_staging_memory_events{{job="{kind}",event="{event}"}} {count}'
        )
    metrics.append(
        f'greenmind_tiered_staging_anon_sampled_peak_bytes{{job="{kind}"}} {value["sampled_anon_peak_bytes"] if proof else "NaN"}'
    )
    target = root / (kind + "-service-result.prom")
    temporary = target.with_suffix(".pending")
    with temporary.open("w") as out:
        os.chmod(temporary, 0o600)
        out.write("\n".join(metrics) + "\n")
        out.flush()
        os.fsync(out.fileno())
    os.replace(temporary, target)
    return value


if __name__ == "__main__":
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
