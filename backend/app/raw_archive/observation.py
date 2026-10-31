"""A finite 24-hour, read-only receiver observation; cannot copy, evict or reload."""

import argparse
import hashlib
import json
import os
import re
import stat
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


def scoped_receivers():
    """Bind a current production deployment without weakening the old default.

    The operator supplies an exact private, hash-pinned local inventory. Never
    discover a different receiver automatically after a restart or replacement.
    """
    name = os.getenv("RAW_ARCHIVE_OBSERVATION_SCOPE_FILE")
    pin = os.getenv("RAW_ARCHIVE_OBSERVATION_SCOPE_SHA256")
    if not name and not pin:
        return None
    if not name or not re.fullmatch(r"[a-f0-9]{64}", pin or ""):
        raise ArchiveBlocked("Explicit observation scope and SHA-256 required")
    path = Path(name)
    if not path.is_absolute():
        raise ArchiveBlocked("Absolute observation scope required")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid not in {0, os.getuid()}
            or info.st_nlink != 1
            or info.st_mode & 0o077
            or info.st_size > 65536
        ):
            raise ArchiveBlocked("Private observation scope required")
        raw = stream.read(65537)
    if len(raw) > 65536 or hashlib.sha256(raw).hexdigest() != pin:
        raise ArchiveBlocked("Observation scope changed")
    scope = json.loads(raw)
    if (
        scope.get("schema") != "greenmind.production-receiver-observation.v1"
        or scope.get("environment") != "production"
        or scope.get("machine_sha256")
        != hashlib.sha256(Path("/etc/machine-id").read_bytes()).hexdigest()
    ):
        raise ArchiveBlocked("Observation scope belongs to another deployment")
    roles = scope.get("receivers")
    required = {"gateway", "direct", "legacy_gateway"}
    if (
        not isinstance(roles, dict)
        or not required <= set(roles)
        or len(roles) > 16
        or any(not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", role) for role in roles)
    ):
        raise ArchiveBlocked("Complete receiver inventory required")
    ids = []
    for target in roles.values():
        if (
            not isinstance(target, dict)
            or set(target) != {"id", "configuration_sha256", "mounted_program_sha256"}
            or any(
                not isinstance(target[k], str) or not re.fullmatch(r"[a-f0-9]{64}", target[k])
                for k in ("id", "configuration_sha256")
            )
        ):
            raise ArchiveBlocked("Immutable receiver identities required")
        programs = target["mounted_program_sha256"]
        if (
            not isinstance(programs, dict)
            or len(programs) > 4096
            or any(
                not isinstance(k, str)
                or not k.startswith("/app/app/")
                or len(k) > 512
                or ".." in Path(k).parts
                or not k.endswith((".py", ".sql"))
                or not isinstance(v, str)
                or not re.fullmatch(r"[a-f0-9]{64}", v)
                for k, v in programs.items()
            )
        ):
            raise ArchiveBlocked("Explicit mounted program fingerprints required")
        ids.append(target["id"])
    if len(ids) != len(set(ids)):
        raise ArchiveBlocked("Distinct receiver roles required")
    return scope


def scoped_container(target):
    """Read Docker metadata only; credentials are hashed in RAM, never emitted."""
    if not isinstance(target.get("id"), str) or not re.fullmatch(r"[a-f0-9]{64}", target["id"]):
        raise ArchiveBlocked("Exact receiver container ID required")
    call = subprocess.run(  # noqa: S603 - exact hexadecimal container ID, no shell
        ["/usr/bin/docker", "inspect", "--type=container", target["id"]],
        capture_output=True,
        timeout=5,
    )
    if call.returncode or call.stderr or len(call.stdout) > 1024**2:
        raise ArchiveBlocked("Bound receiver inspection failed")
    values = json.loads(call.stdout)
    if not isinstance(values, list) or len(values) != 1:
        raise ArchiveBlocked("Bound receiver inspection shape changed")
    value = values[0]
    configuration = {key: value[key] for key in ("Image", "Config", "HostConfig")}
    configuration["Mounts"] = sorted(value["Mounts"], key=lambda item: item["Destination"])
    pin = hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()
    state = value["State"]
    if (
        value["Id"] != target["id"]
        or pin != target["configuration_sha256"]
        or state.get("Running") is not True
        or state.get("Restarting")
        or state.get("OOMKilled")
        or not isinstance(state.get("Pid"), int)
        or state["Pid"] <= 1
    ):
        raise ArchiveBlocked("Bound receiver changed or stopped")
    # A bind mount can change bytes without changing Docker inspect. Bind every
    # mounted program file as well; never read data volumes or credential files.
    code = {}
    total_bytes = 0
    visited = 0
    for mount in value["Mounts"]:
        destination = Path(mount.get("Destination", ""))
        app_root = Path("/app/app")
        if mount.get("Type") != "bind":
            continue
        source = Path(mount["Source"])
        if destination in app_root.parents:
            source = source / app_root.relative_to(destination)
            destination = app_root
        elif destination != app_root and app_root not in destination.parents:
            continue
        if source.is_symlink():
            raise ArchiveBlocked("Mounted programs must not be symlinks")
        files = [source] if source.is_file() else source.rglob("*")
        for program in files:
            visited += 1
            if visited > 8192:
                raise ArchiveBlocked("Mounted program traversal exceeds bound")
            if program.is_symlink():
                raise ArchiveBlocked("Mounted programs must not be symlinks")
            if program.suffix not in {".py", ".sql"}:
                continue
            if source.is_file():
                key = str(destination)
            else:
                key = str(Path(destination) / program.relative_to(source))
            if len(code) >= 4096 or key in code:
                raise ArchiveBlocked("Mounted program inventory exceeds bound")
            fd = os.open(program, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                total_bytes += info.st_size
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_size > 2 * 1024**2
                    or total_bytes > 16 * 1024**2
                ):
                    raise ArchiveBlocked("Bound receiver program is not bounded")
                raw = stream.read(2 * 1024**2 + 1)
                if len(raw) != info.st_size:
                    raise ArchiveBlocked("Mounted program changed during inspection")
                code[key] = hashlib.sha256(raw).hexdigest()
    if code != target["mounted_program_sha256"]:
        raise ArchiveBlocked("Bound receiver program bytes changed")
    return {
        "id": target["id"],
        "configuration_sha256": pin,
        "image": value["Image"],
        "started": state["StartedAt"],
        "restarts": value["RestartCount"],
        "mounted_program_sha256": code,
    }


def protected():
    result = {}
    scope = scoped_receivers()
    if scope is not None:
        result["production_scope_sha256"] = os.environ["RAW_ARCHIVE_OBSERVATION_SCOPE_SHA256"]
        result["receivers"] = {
            role: scoped_container(target) for role, target in scope["receivers"].items()
        }
        # Keep the same ingress and accepted-reader checks as the old mode.
        names = ()
    else:
        names = RECEIVERS
    if scope is None:
        from .source_probe import gateway_ports

        if 8152 in gateway_ports():
            names = (RECEIVERS[0], "gm-peter-receiver-20261010", *RECEIVERS[1:])
    for name in names:
        state = json.loads(
            subprocess.check_output(  # noqa: S603 - fixed inspection-only command
                [
                    "/usr/bin/docker",
                    "inspect",
                    "--format",
                    '{"running":{{.State.Running}},"started":{{json .State.StartedAt}},'
                    '"restarts":{{.RestartCount}},"image":{{json .Image}},'
                    '"memory":{{.HostConfig.Memory}},"swap":{{.HostConfig.MemorySwap}},'
                    '"cpu":{{.HostConfig.NanoCpus}},"restart_policy":{{json .HostConfig.RestartPolicy.Name}}}',
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
    release = os.getenv("RAW_ARCHIVE_OBSERVATION_READER_RELEASE")
    if release:
        from urllib.request import urlopen

        with urlopen("http://127.0.0.1:28141/health", timeout=3) as response:
            if response.status != 200:
                raise ArchiveBlocked("Accepted archive reader is unavailable")
            body = response.read(8193)
        if len(body) > 8192 or json.loads(body).get("release_id") != release:
            raise ArchiveBlocked("Accepted reader release changed")
        result["accepted_archive_reader_release"] = release
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
    headroom_failures = sum(
        row.get("headroom") is not True
        or not isinstance(row.get("guard"), dict)
        or "reason" not in row["guard"]
        or row["guard"]["reason"] is not None
        for row in ordered
    )
    valid_window = (
        now >= datetime.fromisoformat(started_at)
        and len(set(times)) == len(times)
        and all(datetime.fromisoformat(started_at) <= stamp <= now for stamp in times)
    )
    complete = elapsed >= 86400
    passed = (
        complete
        and len(samples) >= 280
        and gap <= 600
        and not errors
        and not changes
        and not unhealthy
        and not headroom_failures
        and valid_window
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
        "headroom_failures": headroom_failures,
        "sample_window_valid": valid_window,
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
