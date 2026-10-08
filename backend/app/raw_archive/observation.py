"""A finite 24-hour, read-only receiver observation; cannot copy, evict or reload."""

import argparse
import hashlib
import json
import os
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .policy import ArchiveBlocked
from .recovery import private_directory

RECEIVERS = (
    "gm-zones-production-2e687bec010a-application-1",
    "gm-direct-production-direct-api-1",
    "greenminddb-backend-1",
)
PROXIES = ("greenmind-prod", "greenmind-staging")


def protected():
    result = {}
    for name in RECEIVERS:
        state = json.loads(
            subprocess.check_output(  # noqa: S603 - fixed inspection-only command
                [
                    "/usr/bin/docker",
                    "inspect",
                    "--format",
                    '{"running":{{.State.Running}},"started":{{json .State.StartedAt}},'
                    '"restarts":{{.RestartCount}}}',
                    name,
                ],
                text=True,
                timeout=5,
            )
        )
        if not state["running"]:
            raise ArchiveBlocked("A protected receiver is stopped")
        result[name] = state
    for name in PROXIES:
        result[name] = hashlib.sha256(
            (Path("/etc/nginx/sites-available") / name).read_bytes()
        ).hexdigest()
    return result


def source_progress():
    """Indexed latest-row reads only; no execution inside any container."""
    import psycopg2

    result = {}
    queries = {
        "gateway": "SELECT id,created_at FROM wav_file ORDER BY created_at DESC,id DESC LIMIT 1",
        "direct": "SELECT segment_id,revision,verified_at FROM direct_revision "
        "WHERE verified_at IS NOT NULL ORDER BY verified_at DESC,segment_id DESC LIMIT 1",
    }
    for kind, query in queries.items():
        uri = os.environ["RAW_ARCHIVE_" + kind.upper() + "_DATABASE_URL"].replace(
            "postgresql+psycopg2:", "postgresql:"
        )
        db = psycopg2.connect(
            uri,
            connect_timeout=3,
            options="-c default_transaction_read_only=on -c statement_timeout=3000 "
            "-c lock_timeout=150 -c jit=off",
        )
        try:
            with db, db.cursor() as cursor:
                cursor.execute("SHOW transaction_read_only")
                if cursor.fetchone()[0] != "on":
                    raise ArchiveBlocked("Observation source must be read-only")
                cursor.execute(query)
                row = cursor.fetchone()
                result[kind] = [str(value) for value in row] if row else None
        finally:
            db.close()
    return result


def assessment(samples, started_at, now):
    """Elapsed time alone is insufficient; gaps and absent progress fail closed."""
    elapsed = (now - datetime.fromisoformat(started_at)).total_seconds()
    ordered = sorted(samples, key=lambda row: row["at"])
    times = [datetime.fromisoformat(row["at"]) for row in ordered]
    edges = [datetime.fromisoformat(started_at), *times, now]
    gap = max((b - a).total_seconds() for a, b in zip(edges, edges[1:], strict=False))
    progress = {
        kind: len(
            {
                json.dumps(row.get("source_progress", {}).get(kind))
                for row in ordered
                if row.get("source_progress", {}).get(kind) is not None
            }
        )
        >= 2
        for kind in ("gateway", "direct")
    }
    errors = sum(row.get("error") is not None for row in ordered)
    changes = sum(not row.get("protected_unchanged", False) for row in ordered)
    unhealthy = sum(not row.get("receiver_health", False) for row in ordered)
    pauses = {}
    for row in ordered:
        reason = row.get("guard", {}).get("reason")
        if reason:
            pauses[reason] = pauses.get(reason, 0) + 1
    complete = elapsed >= 86400
    passed = (
        complete
        and len(samples) >= 280
        and gap <= 600
        and not errors
        and not changes
        and not unhealthy
        and all(progress.values())
    )
    return {
        "observation_complete": complete,
        "receiver_observation_passed": passed,
        "samples": len(samples),
        "elapsed_seconds": round(elapsed),
        "maximum_gap_seconds": round(gap),
        "source_progress": progress,
        "errors": errors,
        "protected_changes": changes,
        "unhealthy_samples": unhealthy,
        "pauses": pauses,
        "deleted_files": 0,
        # This never substitutes for snapshot, restore, full reconciliation or human consent.
        "deletion_authorized": False,
    }


def write_private(path, data):
    with path.open("x") as body:
        os.chmod(path, 0o600)
        json.dump(data, body, indent=2)
        body.write("\n")


def sample(output):
    import httpx

    from .runner import health_probe

    private_directory(output)
    now = datetime.now(UTC)
    baseline = output / "baseline.json"
    if not baseline.exists():
        write_private(baseline, {"started_at": now.isoformat(), "protected": protected()})
    start = json.loads(baseline.read_text())
    journal = output / "samples.jsonl"
    if now < datetime.fromisoformat(start["started_at"]) + timedelta(hours=24):
        row = {"at": now.isoformat(), "deleted_files": 0}
        probe = health_probe()
        try:
            row["protected_unchanged"] = protected() == start["protected"]
            row["headroom"] = probe()
            row["guard"] = probe.last
            urls = os.environ["RAW_ARCHIVE_HEALTH_URLS"].split(",")
            with httpx.Client(timeout=3, trust_env=False) as client:
                row["receiver_health"] = all(client.get(url).status_code == 200 for url in urls)
            if row["headroom"] and row["protected_unchanged"] and row["receiver_health"]:
                row["source_progress"] = source_progress()
        except Exception as error:
            # Exception messages can contain private connection details.
            row["error"] = type(error).__name__
        finally:
            probe.close()
        descriptor = os.open(journal, os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "a") as body:
            body.write(json.dumps(row) + "\n")
            body.flush()
            os.fsync(body.fileno())
    samples = (
        [json.loads(line) for line in journal.read_text().splitlines()] if journal.exists() else []
    )
    result = assessment(samples, start["started_at"], now)
    if result["observation_complete"] and not (output / "finished.json").exists():
        write_private(output / "finished.json", result)
    print(json.dumps(result))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    sample(args.output)
    if time.monotonic() - started > 45:
        raise ArchiveBlocked("Observation exceeded its execution budget")


if __name__ == "__main__":
    main()
