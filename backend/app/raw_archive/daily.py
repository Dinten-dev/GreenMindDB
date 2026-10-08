"""Daily verified offload; copy and deletion have independent disabled-by-default gates."""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from .health import SafetyPause, await_headroom
from .policy import ArchiveBlocked, Ledger, archive_one
from .telemetry import measure
from .worker import exclusive_worker


@dataclass(frozen=True)
class Limits:
    seconds: int = 3600
    bytes: int = 1024**3  # Original bytes, not network bytes (upload + readback).
    references: int = 10000
    page_size: int = 100
    settle_seconds: int = 600
    recheck_days: int = 7

    def guard(self):
        if (
            not 60 <= self.seconds <= 14400
            or not 1 <= self.bytes <= 10 * 1024**3
            or not 4 <= self.references <= 10000
            or not 1 <= self.page_size <= 100
            or not 60 <= self.settle_seconds <= 86400
            or not 1 <= self.recheck_days <= 30
        ):
            raise ArchiveBlocked("Invalid daily backup resource limits")


class Queue:
    """Cursor + work page commit atomically, independent of live application tables."""

    def __init__(self, ledger, binding):
        self.db = ledger.db
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS backup_meta (name TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        self.db.execute("""CREATE TABLE IF NOT EXISTS backup_work (
            kind TEXT NOT NULL, reference TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
            attempt REAL NOT NULL DEFAULT 0, failure TEXT, priority INTEGER NOT NULL DEFAULT 20,
            PRIMARY KEY(kind, reference))""")
        if "priority" not in {row[1] for row in self.db.execute("PRAGMA table_info(backup_work)")}:
            self.db.execute(
                "ALTER TABLE backup_work ADD COLUMN priority INTEGER NOT NULL DEFAULT 20"
            )
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(backup_work)")}
        if "fingerprint" not in columns:
            self.db.execute("ALTER TABLE backup_work ADD COLUMN fingerprint TEXT")
        if "next_check_at" not in columns:
            self.db.execute(
                "ALTER TABLE backup_work ADD COLUMN next_check_at REAL NOT NULL DEFAULT 0"
            )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS backup_recheck ON backup_work(active,next_check_at)"
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS backup_due ON backup_work(active, priority, attempt)"
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS backup_pipeline_due "
            "ON backup_work(active, kind, priority, attempt)"
        )
        value = json.dumps(binding, sort_keys=True)
        old = self.get("binding")
        if old is not None and old != value:
            raise ArchiveBlocked("Journal belongs to different sources/environment/destination")
        self.set("binding", value)
        self.db.commit()

    def get(self, name):
        row = self.db.execute("SELECT value FROM backup_meta WHERE name=?", (name,)).fetchone()
        return row[0] if row else None

    def set(self, name, value):
        self.db.execute(
            "INSERT INTO backup_meta VALUES (?, ?) ON CONFLICT(name) "
            "DO UPDATE SET value=excluded.value",
            (name, value),
        )

    def cursor(self, kind):
        return json.loads(self.get("cursor:" + kind) or "null")

    def enqueue_page(
        self,
        kind,
        references,
        *,
        finished,
        recent=False,
        fingerprints=None,
        now_epoch=None,
        recent_state=None,
    ):
        now_epoch = time.time() if now_epoch is None else now_epoch
        with self.db:
            for reference in references:
                encoded = json.dumps(reference)
                fingerprint = fingerprints.get(encoded) if fingerprints is not None else None
                if fingerprints is not None and fingerprint is None:
                    raise ArchiveBlocked("Source disappeared during catalog scan")
                self.db.execute(
                    """INSERT INTO backup_work(kind, reference, priority, fingerprint)
                    VALUES (?, ?, ?, ?) ON CONFLICT(kind, reference) DO UPDATE SET
                    active=CASE WHEN excluded.fingerprint IS NOT NULL
                        AND excluded.fingerprint=backup_work.fingerprint
                        AND backup_work.next_check_at>? AND backup_work.active=0
                        THEN 0 ELSE 1 END,
                    priority=CASE WHEN backup_work.active=0 THEN excluded.priority
                    ELSE min(backup_work.priority,excluded.priority) END,
                    fingerprint=excluded.fingerprint""",
                    (kind, encoded, 0 if recent else 20, fingerprint, now_epoch),
                )
            if not recent:
                self.set("cursor:" + kind, json.dumps(None if finished else references[-1]))
                if finished:
                    self.set("sweep_finished:" + kind, str(now_epoch))
            elif recent_state is not None:
                self.set("recent:" + kind, json.dumps(None if finished else recent_state))

    def activate_due(self, now_epoch, limit):
        with self.db:
            self.db.execute(
                """UPDATE backup_work SET active=1 WHERE rowid IN (
                SELECT rowid FROM backup_work WHERE active=0 AND next_check_at>0
                AND next_check_at<=? ORDER BY next_check_at LIMIT ?)""",
                (now_epoch, limit),
            )

    def pending(self, limit):
        """Share bounded work across both pipelines and both age classes."""
        if limit < 1:
            return []
        classes = (("direct", 0), ("gateway", 0), ("gateway", 1), ("direct", 1))
        previous = self.get("pending_turn")
        start = int(previous) % len(classes) if previous and previous.isdigit() else 0
        order = classes[start:] + classes[:start]
        groups = {key: [] for key in classes}
        exhausted = set()

        def fetch(key, count):
            if count < 1 or key in exhausted:
                return
            kind, historical = key
            predicate = "priority>0" if historical else "priority=0"
            order_by = "priority,attempt,reference" if historical else "attempt,reference"
            rows = self.db.execute(
                "SELECT kind,reference FROM backup_work WHERE active=1 AND kind=? AND "
                + predicate
                + " ORDER BY "
                + order_by
                + " LIMIT ? OFFSET ?",
                (kind, count, len(groups[key])),
            ).fetchall()
            groups[key].extend(rows)
            if len(rows) < count:
                exhausted.add(key)

        base, extra = divmod(limit, len(classes))
        for index, key in enumerate(order):
            fetch(key, base + (index < extra))
        while (spare := limit - sum(len(rows) for rows in groups.values())) > 0:
            active = sorted(
                (key for key in order if key not in exhausted), key=lambda key: len(groups[key])
            )
            if not active:
                break
            for index, key in enumerate(active):
                count = min(spare, (spare + len(active) - index - 1) // (len(active) - index))
                fetch(key, count)
                spare = limit - sum(len(rows) for rows in groups.values())
                if spare == 0:
                    break
        result = []
        last = None
        for index in range(max(len(rows) for rows in groups.values())):
            for key in order:
                if index < len(groups[key]):
                    result.append(groups[key][index])
                    last = key
        with self.db:
            self.set(
                "pending_turn",
                str((classes.index(last) + 1) % len(classes)) if last else str(start),
            )
        return result

    def attempted(self, kind, reference, *, completed, failure=None, next_check_at=0):
        with self.db:
            self.db.execute(
                """UPDATE backup_work SET active=?, attempt=?, failure=?, next_check_at=?
                WHERE kind=? AND reference=?""",
                (
                    int(not completed),
                    time.time(),
                    failure,
                    next_check_at if completed else 0,
                    kind,
                    reference,
                ),
            )

    def remaining(self):
        return self.db.execute("SELECT count(*) FROM backup_work WHERE active=1").fetchone()[0]


def fresh_receipt(ledger, recording, config, destination, now, days):
    previous = ledger.load(recording)
    if not previous or previous[0] not in {"verified", "evicted"}:
        return False
    receipt = previous[1]
    expected = recording.remote_key(config.namespace)
    if (
        receipt["destination"] != destination.identity
        or receipt["remote_key"] != expected
        or any(receipt["recording"].get(k) != v for k, v in recording.content_identity().items())
    ):
        raise ArchiveBlocked("Verified backup identity changed")
    verified = datetime.fromisoformat(receipt["verified_at"])
    return verified.tzinfo is not None and timedelta(0) <= now - verified < timedelta(days=days)


def run_daily(
    config,
    catalogs,
    sources,
    destination,
    *,
    healthy,
    source_identities,
    limits=Limits(),
    now=None,
    monotonic=time.monotonic,
    eviction_catalogs=None,
    metrics=None,
):
    """Verified daily offload, with source removal only after independent read acceptance.

    Every catalogued finalized WAV is scanned in bounded repeating keyset sweeps.
    Oversized/bad objects remain visible failures, not silently filtered successes.
    Late inserts below a cursor are caught by the next sweep. A job with a budget
    exhausted, unfinished scan or any unresolved reference reports INCOMPLETE.
    """
    config.guard()
    limits.guard()
    if config.delete_enabled:
        config.guard(deleting=True)
        if eviction_catalogs is None or set(eviction_catalogs) != {"gateway", "direct"}:
            raise ArchiveBlocked(
                "Deletion requires independent current feature/projection catalogs"
            )
    copy_config = replace(config, delete_enabled=False)
    if set(catalogs) != {"gateway", "direct"} or set(sources) != set(catalogs):
        raise ArchiveBlocked("Both Gateway and Direct sources must be configured")
    if set(source_identities) != set(catalogs):
        raise ArchiveBlocked("Stable source identities are required")
    now = now or datetime.now(UTC)
    started = monotonic()
    report = dict(
        status="incomplete",
        verified=0,
        attempted=0,
        reused=0,
        bytes=0,
        failed=0,
        scanned=0,
        scans_finished=[],
        errors=[],
        pending=0,
        budget_reached=False,
        started_at=now.isoformat(),
        recent_scans_finished=[],
        deleted=0,
        verified_bytes=0,
    )

    def checkpoint():
        from .coordination import yield_requested

        if yield_requested():
            raise SafetyPause("archive_job_yield")
        if (config.root / "PAUSE").exists():
            raise SafetyPause("manual_pause")
        if monotonic() - started >= limits.seconds:
            raise SafetyPause("runtime_budget")
        if not healthy():
            state = getattr(healthy, "last", {})
            if state.get("reason") == "host_load":
                await_headroom(
                    healthy,
                    deadline=started + limits.seconds,
                    paused=lambda: (config.root / "PAUSE").exists(),
                    metrics=metrics,
                    clock=monotonic,
                )
            else:
                raise SafetyPause(state.get("reason", "receiver_headroom"), state)
        if shutil.disk_usage(config.root).free < config.min_free_bytes:
            raise SafetyPause("scratch_space")

    with exclusive_worker(config):
        ledger = Ledger(config.root)
        queue = None
        try:
            queue = Queue(
                ledger,
                {
                    "namespace": config.namespace,
                    "destination": destination.identity,
                    "sources": source_identities,
                },
            )
            queue.activate_due(now.timestamp(), limits.references)
            before = now - timedelta(seconds=limits.settle_seconds)
            # Discovery is independently bounded, shared fairly between both pipelines.
            per_catalog = limits.references // len(catalogs)
            # Drain an existing queue before repeating thousands of catalog reads.
            # Small discovery pages still admit both new and historical WAVs.
            if queue.remaining() >= limits.references:
                per_catalog = min(per_catalog, 200)
            for kind, catalog in catalogs.items():
                recent_budget = max(1, per_catalog * 2 // 3)
                recent_state = json.loads(queue.get("recent:" + kind) or "null") or {
                    "after": (now - timedelta(days=2)).isoformat(),
                    "before": before.isoformat(),
                    "cursor": None,
                }
                remaining, recent_cursor = recent_budget, recent_state["cursor"]
                while remaining:
                    checkpoint()
                    count = min(limits.page_size, remaining)
                    with measure(metrics, "catalog_scan"):
                        page = catalog.recent_page(
                            recent_cursor,
                            datetime.fromisoformat(recent_state["after"]),
                            datetime.fromisoformat(recent_state["before"]),
                            count,
                        )
                        references = [row[0] for row in page]
                        fingerprints = (
                            catalog.fingerprints(references)
                            if hasattr(catalog, "fingerprints")
                            else None
                        )
                    if len(page) > count:
                        raise ArchiveBlocked("Catalog exceeded requested page size")
                    finished = len(page) < count
                    recent_state["cursor"] = page[-1][1] if page else None
                    queue.enqueue_page(
                        kind,
                        references,
                        fingerprints=fingerprints,
                        now_epoch=now.timestamp(),
                        recent_state=recent_state,
                        finished=finished,
                        recent=True,
                    )
                    report["scanned"] += len(page)
                    remaining -= len(page)
                    if finished:
                        report["recent_scans_finished"].append(kind)
                        break
                    recent_cursor = page[-1][1]
                # Unused recent quota is available for the durable historical sweep.
                remaining += per_catalog - recent_budget
                while remaining:
                    checkpoint()
                    count = min(limits.page_size, remaining)
                    with measure(metrics, "catalog_scan"):
                        page = catalog.page(queue.cursor(kind), before, count)
                        fingerprints = (
                            catalog.fingerprints(page) if hasattr(catalog, "fingerprints") else None
                        )
                    if len(page) > count:
                        raise ArchiveBlocked("Catalog exceeded requested page size")
                    finished = len(page) < count
                    queue.enqueue_page(
                        kind,
                        page,
                        finished=finished,
                        fingerprints=fingerprints,
                        now_epoch=now.timestamp(),
                    )
                    report["scanned"] += len(page)
                    remaining -= len(page)
                    if finished:
                        report["scans_finished"].append(kind)
                        break
            # Snapshot pending references once: a failed item cannot monopolize this run.
            consecutive_failures = 0
            for kind, reference in queue.pending(limits.references):
                checkpoint()
                completed = False
                failure = None
                next_check_at = 0
                try:
                    with measure(metrics, "catalog_resolve"):
                        records = catalogs[kind].resolve(json.loads(reference))
                    if not records:
                        raise ArchiveBlocked("Finalized catalog reference contains no WAV")
                    completed = True
                    for recording in records:
                        checkpoint()
                        recording.eligible(before, config, copying=True)
                        previous = ledger.load(recording)
                        if fresh_receipt(
                            ledger, recording, config, destination, now, limits.recheck_days
                        ) and (not config.delete_enabled or previous[0] == "evicted"):
                            report["reused"] += 1
                            continue
                        if (
                            report["attempted"] >= config.max_files
                            or report["bytes"] + recording.size > limits.bytes
                        ):
                            report["budget_reached"] = True
                            completed = False
                            break
                        # Count attempted transfer bytes too, so errors cannot bypass limits.
                        report["bytes"] += recording.size
                        report["attempted"] += 1
                        copied = archive_one(
                            copy_config,
                            recording,
                            sources[kind],
                            destination,
                            ledger,
                            catalogs[kind].revalidate,
                            copy_only=True,
                            checkpoint=checkpoint,
                            now=lambda: now,
                            metrics=metrics,
                        )
                        report["verified"] += 1
                        report["verified_bytes"] += recording.size
                        if config.delete_enabled and copied != "evicted":
                            strict = eviction_catalogs[kind]
                            candidates = strict.resolve(json.loads(reference))
                            selected = [
                                candidate
                                for candidate in candidates
                                if candidate.content_identity() == recording.content_identity()
                                and candidate.remote_key(config.namespace)
                                == recording.remote_key(config.namespace)
                            ]
                            if len(selected) != 1:
                                raise ArchiveBlocked("Source identity changed before local removal")
                            # A SECOND full readback occurs immediately before exact-version deletion.
                            status = archive_one(
                                config,
                                selected[0],
                                sources[kind],
                                destination,
                                ledger,
                                strict.revalidate,
                                checkpoint=checkpoint,
                                now=lambda: now,
                            )
                            report["deleted"] += int(status == "evicted")
                    if completed and not config.delete_enabled:
                        expiry = [
                            datetime.fromisoformat(ledger.load(recording)[1]["verified_at"])
                            + timedelta(days=limits.recheck_days)
                            for recording in records
                        ]
                        next_check_at = min(stamp.timestamp() for stamp in expiry)
                except SafetyPause:
                    queue.attempted(kind, reference, completed=False, failure="SafetyPause")
                    raise
                except Exception as error:
                    completed = False
                    failure = type(error).__name__
                    report["failed"] += 1
                    # Never serialize arbitrary SDK exceptions (URLs/credentials may leak).
                    item = dict(
                        kind=kind,
                        reference_hash=hashlib.sha256(reference.encode()).hexdigest(),
                        error=failure,
                    )
                    if isinstance(error, ArchiveBlocked):
                        item["reason"] = str(error)
                        failure += ": " + str(error)
                    report["errors"].append(item)
                queue.attempted(
                    kind,
                    reference,
                    completed=completed,
                    failure=failure,
                    next_check_at=next_check_at,
                )
                consecutive_failures = consecutive_failures + 1 if failure else 0
                if consecutive_failures >= 3:
                    report["reason"] = "Three consecutive failures; retry durable queue next run"
                    break
                if report["budget_reached"]:
                    break
            report["pending"] = queue.remaining()
            if (
                not report["pending"]
                and not report["failed"]
                and not report["budget_reached"]
                and len(report["scans_finished"]) == len(catalogs)
                and len(report["recent_scans_finished"]) == len(catalogs)
            ):
                report["status"] = "complete"
            report["finished_at"] = datetime.now(UTC).isoformat()
            with ledger.db:
                queue.set("last_report", json.dumps(report))
            return report
        except ArchiveBlocked as error:
            report.update(
                status="blocked", reason=str(error), finished_at=datetime.now(UTC).isoformat()
            )
            if isinstance(error, SafetyPause):
                report["pause_code"], report["guard"] = error.code, error.details
            if queue is not None:
                report["pending"] = queue.remaining()
                with ledger.db:
                    queue.set("last_report", json.dumps(report))
            return report
        finally:
            if metrics is not None:
                report["telemetry"] = metrics.snapshot()
            if queue is not None:
                report["catalog_sweeps"] = {
                    kind: queue.get("sweep_finished:" + kind) for kind in catalogs
                }
                with ledger.db:
                    queue.set("last_report", json.dumps(report))
            ledger.close()
