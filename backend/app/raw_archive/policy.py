"""Fail-closed copy, independent readback, then separately authorized eviction.

No scheduler, startup hook, migration, or network activity at import time.
The source catalog and feature data remain on the application server.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from .telemetry import measure

ZURICH = ZoneInfo("Europe/Zurich")


class ArchiveBlocked(RuntimeError):
    """Keep the local original when any safety condition is unproven."""


class RemoteMissing(ArchiveBlocked):
    """Explicit missing-file response; connection and integrity failures are different."""


def checksum(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(256 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def cutoff(now: datetime) -> datetime:
    """Start of the current Swiss calendar day; no three-month retention anymore."""
    if now.tzinfo is None:
        raise ArchiveBlocked("A timezone-aware clock is required")
    return now.astimezone(ZURICH).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def sensor_folder(mac: str | None, kind: str, identity: str) -> str:
    """Stable physical address; fall back to the catalog UUID, never a mutable name."""
    if mac and re.fullmatch(r"(?:[0-9a-fA-F]{12}|(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2})", mac):
        compact = mac.lower().replace(":", "").replace("-", "")
        return "mac-" + "-".join(compact[n : n + 2] for n in range(0, 12, 2))
    import uuid

    return kind + "-" + str(uuid.UUID(str(identity)))


@dataclass(frozen=True)
class Config:
    enabled: bool = False
    delete_enabled: bool = False
    # Set only after the archive-aware download paths and restore tests are accepted.
    reads_accepted: bool = False
    root: Path = Path("/var/lib/greenmind-raw-archive")
    namespace: str = "production"
    max_file_bytes: int = 64 * 1024 * 1024
    min_free_bytes: int = 2 * 1024**3
    max_files: int = 10
    local_grace_days: int = 7
    deletion_approval_file: Path | None = None
    deletion_approval_sha256: str = ""
    quarantine_file: Path | None = None

    def guard(self, *, deleting: bool = False, reading: bool = False) -> None:
        if not self.enabled and not reading:
            raise ArchiveBlocked("RAW archive is disabled")
        if not self.root.is_absolute():
            raise ArchiveBlocked("Absolute isolated state directory required")
        if self.namespace not in {"production", "staging"}:
            raise ArchiveBlocked("Explicit environment required")
        if (
            not 1 <= self.max_files <= 10000
            or not 1 <= self.max_file_bytes <= 64 * 1024**2
            or self.min_free_bytes < 0
        ):
            raise ArchiveBlocked("Invalid resource bounds")
        if deleting and not (self.delete_enabled and self.reads_accepted):
            raise ArchiveBlocked("Local deletion and archived reads require separate acceptance")
        if deleting and not 7 <= self.local_grace_days <= 365:
            raise ArchiveBlocked("At least seven days of local reserve are required")
        if deleting and any(
            os.environ.get(key, "").lower() in {"1", "true", "yes"}
            for key in (
                "RETENTION_ENABLED",
                "DIRECT_RETENTION_ENABLED",
            )
        ):
            raise ArchiveBlocked("Legacy destructive retention must remain disabled")


@dataclass(frozen=True)
class Recording:
    kind: str
    identity: str
    bucket: str
    key: str
    sha256: str
    size: int
    ended_at: datetime
    received_at: datetime
    feature_digest: str
    started_at: datetime | None = None
    sensor: str = ""

    @property
    def archive_id(self) -> str:
        value = json.dumps([self.kind, self.bucket, self.key], separators=(",", ":"))
        return hashlib.sha256(value.encode()).hexdigest()

    def eligible(self, now: datetime, config: Config, *, copying: bool = False) -> None:
        if self.kind not in {"gateway", "direct"} or not self.key.endswith(".wav"):
            raise ArchiveBlocked("Only catalogued RAW WAV objects may be archived")
        for value in (self.sha256,) if copying else (self.sha256, self.feature_digest):
            if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value):
                raise ArchiveBlocked("Verified source and feature checksums are required")
        if not 0 < self.size <= config.max_file_bytes:
            raise ArchiveBlocked("WAV exceeds the isolated worker size budget")
        limit = now if copying else cutoff(now)
        if any(t.tzinfo is None or t > limit for t in (self.ended_at, self.received_at)):
            raise ArchiveBlocked("WAV is outside the permitted completion/age boundary")
        self.remote_key(config.namespace)

    def remote_key(self, namespace: str) -> str:
        if (
            namespace not in {"production", "staging"}
            or self.started_at is None
            or self.started_at.tzinfo is None
            or self.started_at > self.ended_at
            or not re.fullmatch(
                r"(?:mac-(?:[0-9a-f]{2}-){5}[0-9a-f]{2}|"
                r"(?:gateway|direct)-[0-9a-f-]{36})",
                self.sensor,
            )
        ):
            raise ArchiveBlocked("Verified recording start and stable sensor identity required")
        stamp = self.started_at.astimezone(ZURICH)
        return (
            f"{namespace}/{stamp:%Y-%m-%d}/{self.sensor}/"
            f"{stamp:%H%M%S%f}_{self.kind}_{self.archive_id}_{self.sha256}.wav"
        )

    @classmethod
    def from_receipt(cls, value):
        parsed = dict(value)
        for name in ("started_at", "ended_at", "received_at"):
            if parsed.get(name):
                parsed[name] = datetime.fromisoformat(parsed[name])
        return cls(**parsed)

    def content_identity(self) -> dict:
        """Features and catalog revisions can change without changing immutable bytes."""
        return {name: getattr(self, name) for name in ("kind", "bucket", "key", "sha256", "size")}


class Source(Protocol):
    def snapshot(self, recording: Recording) -> dict: ...
    def download(self, recording: Recording, snapshot: dict, path: Path) -> None: ...
    def evict(self, recording: Recording, snapshot: dict) -> None: ...


class Destination(Protocol):
    @property
    def identity(self) -> str: ...
    def publish(self, path: Path, key: str) -> None: ...
    def download(self, key: str, path: Path, max_bytes: int) -> None: ...


class Ledger:
    """Separate durable journal; never adds tables or writes to the live database."""

    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = root / "archive.sqlite3"
        self.db = sqlite3.connect(self.path, timeout=2)
        os.chmod(self.path, 0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS archive (
            id TEXT PRIMARY KEY, state TEXT NOT NULL, receipt TEXT NOT NULL)""")
        self.db.commit()

    def save(self, recording: Recording, state: str, receipt: dict) -> None:
        self.db.execute(
            "INSERT INTO archive VALUES (?, ?, ?) ON CONFLICT(id) "
            "DO UPDATE SET state=excluded.state, receipt=excluded.receipt",
            (recording.archive_id, state, json.dumps(receipt, sort_keys=True)),
        )
        self.db.commit()

    def load(self, recording: Recording) -> tuple[str, dict] | None:
        row = self.db.execute(
            "SELECT state, receipt FROM archive WHERE id=?", (recording.archive_id,)
        ).fetchone()
        return (row[0], json.loads(row[1])) if row else None

    def close(self) -> None:
        self.db.close()


def archive_one(
    config: Config,
    recording: Recording,
    source: Source,
    destination: Destination,
    ledger: Ledger,
    revalidate: Callable[[Recording], None],
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    checkpoint: Callable[[], None] = lambda: None,
    copy_only: bool = False,
    metrics=None,
) -> str:
    """One bounded file. Any exception preserves the source unless eviction completed.

    revalidate must re-read and verify persisted features, source identity, age,
    and dashboard projection coverage. It must not return cached ORM objects.
    A durable deleting receipt makes a crash after S3 deletion recoverable.
    """

    def timed(stage, operation, *args):
        with measure(metrics, stage):
            return operation(*args)

    config.guard()
    if copy_only and config.delete_enabled:
        raise ArchiveBlocked("Copy-only invocation cannot enable deletion")
    recording.eligible(now(), config, copying=copy_only)
    previous = ledger.load(recording)
    key = recording.remote_key(config.namespace)
    if previous:
        receipt = previous[1]
        if (
            receipt["remote_key"] != key
            or receipt["destination"] != destination.identity
            or any(
                receipt["recording"].get(k) != v for k, v in recording.content_identity().items()
            )
        ):
            raise ArchiveBlocked("Archive receipt identity changed")
    if shutil.disk_usage(config.root).free < config.min_free_bytes + recording.size * 3:
        raise ArchiveBlocked("Insufficient isolated scratch space")
    checkpoint()
    timed("catalog_revalidate", revalidate, recording)
    with tempfile.TemporaryDirectory(prefix="wav-", dir=config.root) as directory:
        local = Path(directory) / "source.wav"
        returned = Path(directory) / "readback.wav"
        # Resume only a receipt already committed before a previous deletion attempt.
        pending = previous and previous[0] in {"verified", "deleting", "evicted"}
        if pending:
            receipt = previous[1]
            snapshot = receipt["snapshot"]
        else:
            snapshot = timed("source_metadata", source.snapshot, recording)
            timed("source_read", source.download, recording, snapshot, local)
            if (
                local.stat().st_size != recording.size
                or timed("checksum", checksum, local) != recording.sha256
            ):
                raise ArchiveBlocked("Local WAV differs from its catalog checksum")
            checkpoint()
            timed("upload", destination.publish, local, key)
            receipt = {
                "recording": json.loads(json.dumps(asdict(recording), default=str)),
                "snapshot": snapshot,
                "remote_key": key,
                "destination": destination.identity,
            }
        # Read the final remote path, not the temporary upload or a remote stat alone.
        checkpoint()
        try:
            timed("readback", destination.download, key, returned, config.max_file_bytes)
        except RemoteMissing as error:
            if not copy_only or not pending or previous[0] != "verified":
                raise
            # Repair a provably missing backup from the still-local original only.
            # A damaged/unreachable remote never enters this recovery path.
            returned.unlink(missing_ok=True)
            snapshot = timed("source_metadata", source.snapshot, recording)
            timed("source_read", source.download, recording, snapshot, local)
            if (
                local.stat().st_size != recording.size
                or timed("checksum", checksum, local) != recording.sha256
            ):
                raise ArchiveBlocked(
                    "Local source changed; missing backup cannot be repaired"
                ) from error
            checkpoint()
            timed("upload", destination.publish, local, key)
            checkpoint()
            timed("readback", destination.download, key, returned, config.max_file_bytes)
            receipt["snapshot"] = snapshot
        if (
            returned.stat().st_size != recording.size
            or timed("checksum", checksum, returned) != recording.sha256
        ):
            raise ArchiveBlocked("Storage Box readback failed; local original retained")
        # Earlier receipts have only the latest successful verification time.
        # Using that as the first time is conservative, never backdates consent.
        receipt.setdefault("first_verified_at", receipt.get("verified_at", now().isoformat()))
        receipt["verified_at"] = now().isoformat()
        receipt["recording"] = json.loads(json.dumps(asdict(recording), default=str))
        ledger.save(recording, previous[0] if pending else "verified", receipt)
        if previous and previous[0] == "evicted":
            return "evicted"
        if not config.delete_enabled:
            return "verified"
        checkpoint()
        config.guard(deleting=True)
        recording.eligible(now(), config)
        timed("catalog_revalidate", revalidate, recording)
        from .deletion import verify_deletion_evidence

        verify_deletion_evidence(config, recording, receipt, destination, now())
        ledger.save(recording, "deleting", receipt)
        source.evict(recording, snapshot)
        ledger.save(recording, "evicted", receipt)
        return "evicted"
