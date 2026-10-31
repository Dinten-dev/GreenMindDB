"""Bounded resumable SQL sweeps and proof refresh; activation stays separate."""

import hashlib
import json
import os
import stat
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.raw_archive.health import SafetyPause
from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.remote_lease import source_checkpoint, source_copy_only

from .codec import decode, encode
from .latency import health_probe
from .ledger import Ledger
from .pipeline import SnapshotPending
from .policy import utc
from .runtime import environment, state_root
from .source import DATASETS
from .wav_queue import WavQueue


def private_json(path, maximum=1024 * 1024):
    with Path(path).open("rb") as body:
        info = os.fstat(body.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o077
            or info.st_uid not in {0, os.getuid()}
        ):
            raise ArchiveBlocked("Private operator-owned schedule required")
        raw = body.read(maximum + 1)
    if len(raw) > maximum:
        raise ArchiveBlocked("Schedule exceeds budget")
    return json.loads(raw)


def exact_wav_jobs(plan):
    """An exact copy pilot never opens a dataset discovery/SQL/backup path."""
    if type(plan) is not dict or set(plan) != {"schema", "environment", "wav_jobs", "wav_limits"}:
        raise ArchiveBlocked("Exact WAV pilot cannot mix scanning, snapshots or other operations")
    if type(plan["schema"]) is not int or plan["schema"] != 1:
        raise ArchiveBlocked("Exact WAV pilot schema differs")
    if type(plan["environment"]) is not str or plan["environment"] not in {"production", "staging"}:
        raise ArchiveBlocked("Explicit exact WAV pilot environment required")
    jobs, limits = plan["wav_jobs"], plan["wav_limits"]
    if type(jobs) is not list or not 1 <= len(jobs) <= 10:
        raise ArchiveBlocked("One to ten exact WAV jobs required")
    if (
        type(limits) is not dict
        or set(limits) != {"max_wavs", "max_bytes"}
        or type(limits["max_wavs"]) is not int
        or not 1 <= limits["max_wavs"] <= 10
        or type(limits["max_bytes"]) is not int
        or not 1 <= limits["max_bytes"] <= 5 * 1024**2
    ):
        raise ArchiveBlocked("Exact WAV pilot is bounded by ten WAVs or five MiB")
    seen = set()
    for job in jobs:
        if type(job) is not dict or set(job) != {"source", "reference"}:
            raise ArchiveBlocked("Exact WAV source and reference required")
        source, reference = job["source"], job["reference"]
        if (
            type(source) is not str
            or source not in {"gateway", "direct"}
            or type(reference) is not list
            or len(reference) != (1 if source == "gateway" else 2)
            or type(reference[0]) is not str
        ):
            raise ArchiveBlocked("Exact WAV reference shape differs")
        try:
            canonical = str(uuid.UUID(reference[0]))
        except ValueError:
            raise ArchiveBlocked("Canonical WAV or segment UUID required") from None
        if canonical != reference[0] or (
            source == "direct"
            and (type(reference[1]) is not int or not 1 <= reference[1] <= 2**31 - 1)
        ):
            raise ArchiveBlocked("Canonical exact WAV revision required")
        identity = (source, tuple(reference))
        if identity in seen:
            raise ArchiveBlocked("Duplicate exact WAV job")
        seen.add(identity)
    return jobs, limits


def tick_exact_wavs(plan, *, clock, seconds):
    from .runtime import stage_wav

    jobs, limits = exact_wav_jobs(plan)  # Before state, source keys or connections.
    if type(seconds) not in {int, float} or not 0 < seconds <= 300:
        raise ArchiveBlocked("Bounded exact WAV pilot lifetime required")
    deadline = time.monotonic() + min(75, seconds)
    closed_before = utc(clock()).replace(hour=0, minute=0, second=0, microsecond=0)
    result = {
        "published": 0,
        "verified": 0,
        "evicted": 0,
        "blocked": 0,
        "deferred": 0,
        "copied_wavs": 0,
        "copied_wav_bytes": 0,
    }
    source_checkpoint()
    root = state_root()
    healthy = health_probe("copy")
    try:

        def checkpoint():
            source_checkpoint()
            if (root / "PAUSE").exists() or (root / "control/PAUSE").exists():
                raise SafetyPause("manual_pause")
            if not healthy():
                raise SafetyPause(healthy.last.get("reason", "receiver_headroom"))

        def can_continue():
            checkpoint()
            return time.monotonic() < deadline

        checkpoint()
        for index, job in enumerate(jobs):
            if (
                result["copied_wavs"] >= limits["max_wavs"]
                or result["copied_wav_bytes"] >= limits["max_bytes"]
                or not can_continue()
            ):
                result["deferred"] += len(jobs) - index
                break
            remaining_wavs = limits["max_wavs"] - result["copied_wavs"]
            remaining_bytes = limits["max_bytes"] - result["copied_wav_bytes"]
            # The existing revision journal resumes exact partial copies. Any
            # exception aborts this invocation: transfers before failure may
            # already consume budget, so never continue another job on error.
            batch = stage_wav(
                job["source"],
                job["reference"],
                maximum_wavs=remaining_wavs,
                maximum_bytes=remaining_bytes,
                maximum_object_bytes=limits["max_bytes"],
                closed_before=closed_before,
                can_continue=can_continue,
            )
            count, size = batch["copied_wavs"], batch["copied_bytes"]
            if (
                type(count) is not int
                or not 0 <= count <= remaining_wavs
                or type(size) is not int
                or not 0 <= size <= remaining_bytes
                or (count == 0) != (size == 0)
                or type(batch["complete"]) is not bool
            ):
                raise ArchiveBlocked("Exact WAV copy accounting differs")
            result["copied_wavs"] += count
            result["copied_wav_bytes"] += size
            result["published"] += int(batch["complete"])
            result["deferred"] += int(not batch["complete"])
        result["pending_wavs"] = result["deferred"]  # Selected jobs only, no backlog scan.
        return result
    finally:
        healthy.close()


def tick(path, *, clock=lambda: datetime.now(UTC), seconds=300):
    from .deletion import activation, limits
    from .policy import eligibility
    from .runtime import (
        backup as database_backup,
    )
    from .runtime import (
        candidate_from_json,
        evict,
        latest_snapshot,
        publish_sql,
        publish_wav,
        stage_wav,
        verify,
        wav_source,
    )

    source_checkpoint()  # Before reading the plan or opening the local ledger.
    plan = private_json(path)
    if plan.get("environment") != environment() or plan.get("schema") != 1:
        raise ArchiveBlocked("Schedule environment differs")
    if "wav_jobs" in plan:
        return tick_exact_wavs(plan, clock=clock, seconds=seconds)
    if "wav_limits" in plan:
        raise ArchiveBlocked("WAV pilot limits require exact WAV jobs")
    tasks = plan.get("scopes", [])
    wav_sources = plan.get("wav_sources", [])
    backups = plan.get("backups", [])
    if (
        not 0 <= len(tasks) <= 256
        or any(source not in {"gateway", "direct"} for source in wav_sources)
        or len(backups) > 2
        or any(source not in {"gateway", "direct"} for source in backups)
        or not (tasks or wav_sources or backups)
    ):
        raise ArchiveBlocked("Explicit bounded dataset scopes required")
    started = time.monotonic()
    deadline = started + min(300, seconds)
    copy_deadline = started + min(150, seconds / 2)
    source_checkpoint()
    ledger = Ledger(state_root())
    db = ledger.db
    db.execute(
        "CREATE TABLE IF NOT EXISTS sweep(id TEXT PRIMARY KEY, cursor BLOB, day TEXT, due TEXT)"
    )
    db.commit()
    result = {
        "published": 0,
        "verified": 0,
        "evicted": 0,
        "blocked": 0,
        "deferred": 0,
        "copied_wavs": 0,
        "copied_wav_bytes": 0,
    }
    pending = WavQueue(db)
    copied_wavs = copied_bytes = 0
    small_staging = environment() == "staging"
    planning_health = None
    try:
        planning_health = health_probe("copy")

        def planning_checkpoint():
            source_checkpoint()
            if (ledger.root / "PAUSE").exists() or (ledger.root / "control/PAUSE").exists():
                raise SafetyPause("manual_pause")
            if not planning_health():
                raise SafetyPause(planning_health.last.get("reason", "receiver_headroom"))

        planning_checkpoint()
        for source in backups:
            planning_checkpoint()
            if time.monotonic() >= copy_deadline:
                break
            identity = "database-backup-" + source
            state = db.execute("SELECT due FROM sweep WHERE id=?", (identity,)).fetchone()
            if state and utc(state[0]) > clock():
                continue
            database_backup(source)
            result["published"] += 1
            with db:
                db.execute(
                    "INSERT INTO sweep VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET due=excluded.due",
                    (
                        identity,
                        None,
                        clock().isoformat(),
                        (clock() + timedelta(hours=12)).isoformat(),
                    ),
                )
        for source in wav_sources:
            planning_checkpoint()
            if time.monotonic() >= copy_deadline:
                break
            identity = "wav-sweep-" + source
            adapter = wav_source(source, planning_checkpoint, copy_published_history=True)
            before = clock().replace(hour=0, minute=0, second=0, microsecond=0)

            handled = set()

            def advance(reference, identity=identity, before=before):
                db.execute(
                    "INSERT INTO sweep VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET cursor=excluded.cursor,due=excluded.due",
                    (identity, encode(reference), before.isoformat(), clock().isoformat()),
                )

            def process(
                reference,
                *,
                discovered,
                source=source,
                adapter=adapter,
                handled=handled,
                advance=advance,
            ):
                nonlocal copied_wavs, copied_bytes
                if time.monotonic() >= copy_deadline:
                    return False
                token = encode(reference)
                if token in handled:
                    if discovered:
                        with db:
                            advance(reference)
                    return True
                waiting = pending.get(source, reference)
                if waiting and utc(waiting[0]) > clock():
                    if discovered:
                        with db:
                            advance(reference)
                    return True
                if small_staging and (copied_wavs >= 10 or copied_bytes >= 5 * 1024**2):
                    return False
                try:
                    planning_checkpoint()
                    planned = adapter.catalog.resolve(reference)
                    total = sum(record.size for record in planned)
                    if small_staging and (total > 5 * 1024**2):
                        with db:
                            pending.defer(
                                source,
                                reference,
                                planned,
                                clock(),
                                max_wavs=10,
                                max_bytes=5 * 1024**2,
                            )
                            if discovered:
                                advance(reference)
                        handled.add(token)
                        result["deferred"] += 1
                        result["blocked"] += 1
                        return True
                    if len(planned) > (10 if small_staging else 127):
                        # Leave capacity for the following revision. A fragmented
                        # capture must neither starve small arrivals nor stay
                        # deferred forever just because it has many WAV runs.
                        remaining = 10 - copied_wavs if small_staging else 127
                        batch = stage_wav(
                            source,
                            reference,
                            maximum_wavs=max(1, remaining // 2) if small_staging else remaining,
                            maximum_bytes=5 * 1024**2 - copied_bytes
                            if small_staging
                            else 64 * 1024**2,
                            can_continue=lambda: time.monotonic() < copy_deadline,
                        )
                        copied_wavs += batch["copied_wavs"]
                        copied_bytes += batch["copied_bytes"]
                        result["copied_wavs"] += batch["copied_wavs"]
                        result["copied_wav_bytes"] += batch["copied_bytes"]
                        with db:
                            if batch["complete"]:
                                pending.published(source, reference, batch["receipt"], clock())
                            else:
                                pending.defer(
                                    source,
                                    reference,
                                    planned,
                                    clock(),
                                    max_wavs=10,
                                    max_bytes=5 * 1024**2,
                                    reason="copy_in_progress",
                                    retry_delay=timedelta(minutes=5),
                                )
                            if discovered:
                                advance(reference)
                        result["published"] += int(batch["complete"])
                        result["deferred"] += int(not batch["complete"])
                        handled.add(token)
                        return True
                    if small_staging and (
                        copied_wavs + len(planned) > 10 or copied_bytes + total > 5 * 1024**2
                    ):
                        # Fits a fresh run: retain its cursor for the next tick.
                        return False
                    receipt = publish_wav(
                        source,
                        reference,
                        maximum_bytes=5 * 1024**2 - copied_bytes if small_staging else None,
                        maximum_wavs=10 - copied_wavs if small_staging else None,
                    )
                    copied_wavs += sum(f["format"] == "wav" for f in receipt["files"])
                    copied_bytes += sum(
                        f["bytes"] for f in receipt["files"] if f["format"] == "wav"
                    )
                    result["copied_wavs"] += sum(f["format"] == "wav" for f in receipt["files"])
                    result["copied_wav_bytes"] += sum(
                        f["bytes"] for f in receipt["files"] if f["format"] == "wav"
                    )
                    result["published"] += 1
                    with db:
                        pending.published(source, reference, receipt, clock())
                        if discovered:
                            advance(reference)
                    handled.add(token)
                except SafetyPause:
                    raise
                except ArchiveBlocked:
                    if small_staging:
                        raise
                    result["blocked"] += 1
                    # Previously queued entries stay pending on validation errors.
                    if discovered:
                        with db:
                            advance(reference)
                return True

            # Retry independently of the daily sweep; never move its cursor
            # backwards when processing an older pending revision.
            planning_checkpoint()
            for reference in pending.due(source, clock()):
                if not process(reference, discovered=False):
                    break
            state = db.execute(
                "SELECT cursor,day,due FROM sweep WHERE id=?", (identity,)
            ).fetchone()
            if state and utc(state[2]) > clock():
                continue
            cursor = decode(state[0]) if state and state[0] else None
            planning_checkpoint()
            references = adapter.catalog.page(cursor, before, 10)
            for reference in references:
                if not process(reference, discovered=True):
                    break
            if not references:
                with db:
                    db.execute(
                        "INSERT INTO sweep VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET cursor=NULL,due=excluded.due",
                        (
                            identity,
                            None,
                            before.isoformat(),
                            (clock() + timedelta(days=1)).isoformat(),
                        ),
                    )
        from collections import deque

        queue = deque(tasks)
        while queue:
            planning_checkpoint()
            task = queue.popleft()
            if time.monotonic() >= copy_deadline:
                break
            dataset = task["dataset"]
            if dataset not in DATASETS:
                raise ArchiveBlocked("Unknown dataset remains blocked")
            identity = hashlib.sha256(json.dumps(task, sort_keys=True).encode()).hexdigest()
            state = db.execute(
                "SELECT cursor,day,due FROM sweep WHERE id=?", (identity,)
            ).fetchone()
            if state and utc(state[2]) > clock():
                continue
            # Closed UTC day partitions; a completed sweep is revisited daily so
            # inserts/changes after the cursor are not silently lost forever.
            day = utc(state[1]) if state else utc(task["start"])
            cursor = decode(state[0]) if state and state[0] else None
            end = min(
                day + timedelta(days=1), clock().replace(hour=0, minute=0, second=0, microsecond=0)
            )
            if end <= day:
                continue
            out = publish_sql(
                DATASETS[dataset].source, dataset, task["organization"], day, end, after=cursor
            )
            result["published"] += int(out.get("status") != "empty")
            full = out.get("full_page", False)
            next_cursor = encode(out["next_cursor"]) if full else None
            next_day = day if full else end
            due = clock()
            if next_day >= clock().replace(hour=0, minute=0, second=0, microsecond=0):
                next_day = utc(task["start"])
                due += timedelta(days=1)
            with db:
                db.execute(
                    "INSERT INTO sweep VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET cursor=excluded.cursor,day=excluded.day,due=excluded.due",
                    (identity, next_cursor, next_day.isoformat(), due.isoformat()),
                )
            if due <= clock():
                queue.append(task)
        planning_checkpoint()
        snapshot_id = plan.get("snapshot_id") or (
            latest_snapshot() if plan.get("scheduled_snapshots") else None
        )
        deleted_wavs = deleted_bytes = deleted_sql = 0
        for (raw,) in db.execute("SELECT receipt FROM bundle ORDER BY start"):
            planning_checkpoint()
            if time.monotonic() >= deadline:
                break
            receipt = json.loads(raw)
            if receipt["state"] == "evicted":
                continue
            if receipt["state"] == "verified" and not all(
                receipt["source"].get(name)
                for name in (
                    "complete",
                    "features_verified",
                    "projections_verified",
                    "dependencies_complete",
                )
            ):
                continue
            if snapshot_id and (
                receipt["state"] == "published"
                or clock() - utc(receipt["verified_at"]) >= timedelta(hours=12)
            ):
                try:
                    verify(receipt["candidate"], snapshot_id)
                    result["verified"] += 1
                except SnapshotPending:
                    # The selected snapshot can legitimately predate a new
                    # upload from this very tick. Retain its published state;
                    # a later snapshot must pass the full independent check.
                    result["deferred"] += 1
                    continue
                except SafetyPause:
                    raise
                except ArchiveBlocked:
                    if small_staging:
                        raise
                    result["blocked"] += 1
                    continue
            if (
                not source_copy_only()
                and os.getenv("TIERED_ARCHIVE_DELETE_ENABLED", "false") == "true"
            ):
                if receipt["source"].get("kind") != "wav":
                    continue
                if receipt["source"].get("active"):
                    continue
                try:
                    current = ledger.get(receipt["candidate"])
                    candidate = candidate_from_json(current["source"])
                    eligibility(candidate, current, clock())
                    permit = activation(
                        os.environ["TIERED_ARCHIVE_ACTIVATION_FILE"],
                        os.environ["TIERED_ARCHIVE_ACTIVATION_SHA256"],
                        candidate,
                        clock(),
                    )
                    budget = limits(permit)
                    wavs = [
                        reference for reference in current["files"] if reference["format"] == "wav"
                    ]
                    size = sum(reference["bytes"] for reference in wavs)
                    if wavs and (
                        deleted_wavs + len(wavs) > budget["wavs"]
                        or deleted_bytes + size > budget["wav_bytes"]
                    ):
                        continue
                    if not wavs and deleted_sql >= budget["sql_partitions"]:
                        continue
                    evict(receipt["candidate"])
                    deleted_wavs += len(wavs)
                    deleted_bytes += size
                    deleted_sql += int(not wavs)
                    result["evicted"] += 1
                except SafetyPause:
                    raise
                except ArchiveBlocked:
                    result["blocked"] += 1
                    continue
        if any(result[name] for name in ("published", "verified", "evicted")):
            from .registry_backup import backup
            from .runtime import worker

            with worker() as (current, store, checkpoint):
                reference = backup(current, store, checkpoint=checkpoint)
                temporary = state_root() / "catalog-reference.pending"
                with temporary.open("w") as body:
                    os.chmod(temporary, 0o600)
                    json.dump(reference, body, sort_keys=True)
                    body.flush()
                    os.fsync(body.fileno())
                os.replace(temporary, state_root() / "catalog-reference.json")
        result["pending_wavs"] = pending.count()
        return result
    finally:
        if planning_health is not None:
            planning_health.close()
        ledger.close()
