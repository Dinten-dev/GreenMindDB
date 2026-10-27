"""Pinned Staging latency baseline and restart-safe consecutive-sample pause."""

import fcntl
import hashlib
import json
import math
import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path

from app.raw_archive.health import SafetyPause
from app.raw_archive.policy import ArchiveBlocked

from .resources import pause, write_json


def read_json(path, maximum=65536):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as body:
        info = os.fstat(body.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid not in {0, os.getuid()}
            or info.st_mode & 0o022
        ):
            raise ArchiveBlocked("Trusted latency evidence required")
        raw = body.read(maximum + 1)
    if len(raw) > maximum:
        raise ArchiveBlocked("Latency evidence exceeds budget")
    return raw, json.loads(raw)


class LatencyGuard:
    def __init__(self, baseline, fingerprint, urls, directory, control, kind):
        if kind not in {"copy", "reader"}:
            raise ArchiveBlocked("Unknown latency guard role")
        if not re.fullmatch("[0-9a-f]{64}", fingerprint):
            raise ArchiveBlocked("Pinned latency baseline required")
        if not Path(baseline).is_absolute():
            raise ArchiveBlocked("Absolute latency baseline path required")
        raw, value = read_json(baseline)
        if hashlib.sha256(raw).hexdigest() != fingerprint:
            raise ArchiveBlocked("Latency baseline changed")
        if not isinstance(value, dict):
            raise ArchiveBlocked("Invalid latency baseline")
        endpoints = value.get("endpoints", [])
        if (
            value.get("schema") != 1
            or value.get("environment") != "staging"
            or value.get("statistic") != "p95-nearest-rank"
            or not isinstance(endpoints, list)
            or any(not isinstance(endpoint, dict) for endpoint in endpoints)
            or len(endpoints) != len(urls)
            or not 2 <= len(endpoints) <= 8
            or [endpoint.get("url") for endpoint in endpoints] != urls
        ):
            raise ArchiveBlocked("Latency baseline belongs to different health endpoints")
        self.thresholds = []
        for endpoint in endpoints:
            baseline_ms = endpoint.get("baseline_ms")
            count = endpoint.get("samples")
            if (
                type(baseline_ms) not in {int, float}
                or not math.isfinite(baseline_ms)
                or not 0 < baseline_ms <= 1000
                or type(count) is not int
                or count < 30
            ):
                raise ArchiveBlocked("Measured healthy latency baseline required")
            self.thresholds.append(max(1.2 * baseline_ms, baseline_ms + 100))
        self.directory, self.control = Path(directory), Path(control)
        if any(
            not path.is_absolute() or path.is_symlink() for path in (self.directory, self.control)
        ):
            raise ArchiveBlocked("Isolated latency state required")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.kind, self.fingerprint = kind, fingerprint
        self.path = self.directory / ("latency-" + kind + ".json")

    @staticmethod
    def sync_directory(path):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def latch(self, reason):
        pause(self.control, self.kind, [reason])
        self.sync_directory(self.control)

    def stop(self, reason):
        self.latch(reason)
        raise SafetyPause(reason)

    def observe(self, milliseconds):
        if len(milliseconds) != len(self.thresholds) or any(
            type(value) not in {int, float} or not math.isfinite(value) or value < 0
            for value in milliseconds
        ):
            self.stop("latency_sample_invalid")
        # Different workers/requests may rebuild probes. Persist the streak and
        # serialize updates so restarts or concurrent reads cannot reset it.
        lock = self.path.with_suffix(".lock")
        fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "r+b") as body:
            fcntl.flock(body, fcntl.LOCK_EX)
            counts = [0] * len(self.thresholds)
            try:
                if self.path.exists() or self.path.is_symlink():
                    _, old = read_json(self.path)
                    if (
                        old.get("schema") != 1
                        or not isinstance(old.get("consecutive"), list)
                        or len(old["consecutive"]) != len(counts)
                        or any(type(n) is not int or not 0 <= n <= 3 for n in old["consecutive"])
                        or not re.fullmatch("[0-9a-f]{64}", old.get("baseline_sha256", ""))
                    ):
                        raise ValueError("Invalid latency state")
                    if old["baseline_sha256"] == self.fingerprint:
                        counts = old["consecutive"]
                counts = [
                    min(3, count + 1) if elapsed > threshold else 0
                    for count, elapsed, threshold in zip(
                        counts, milliseconds, self.thresholds, strict=True
                    )
                ]
                value = {
                    "schema": 1,
                    "baseline_sha256": self.fingerprint,
                    "checked_at": datetime.now(UTC).isoformat(),
                    "consecutive": counts,
                    "latency_ms": milliseconds,
                    "thresholds_ms": self.thresholds,
                    "paused": any(count >= 3 for count in counts),
                }
                if value["paused"]:
                    # Persist the latch before the state says "paused". A crash
                    # after saving counters must not allow the next job to run.
                    self.latch("receiver_latency")
                write_json(self.path, value)
                self.sync_directory(self.directory)
            except (OSError, ValueError, TypeError, AttributeError, KeyError, ArchiveBlocked):
                self.stop("latency_state_invalid")
            if value["paused"]:
                self.stop("receiver_latency")
        return value


def health_probe(kind):
    """Reuse existing host/HTTP guards; Staging additionally requires a baseline."""
    from app.raw_archive.runner import health_probe as existing_probe

    observer = None
    if os.environ.get("TIERED_ARCHIVE_ENVIRONMENT") == "staging":
        root = Path(os.environ["TIERED_ARCHIVE_STATE_DIR"])
        baseline = os.environ.get("TIERED_ARCHIVE_LATENCY_BASELINE")
        if not baseline:
            raise ArchiveBlocked("Staging requires a reviewed latency baseline")
        observer = LatencyGuard(
            Path(baseline),
            os.environ.get("TIERED_ARCHIVE_LATENCY_BASELINE_SHA256", ""),
            os.environ.get("RAW_ARCHIVE_HEALTH_URLS", "").split(","),
            root / "cache" if kind == "reader" else root,
            root / "control",
            kind,
        ).observe
    return existing_probe(on_sample=observer)
