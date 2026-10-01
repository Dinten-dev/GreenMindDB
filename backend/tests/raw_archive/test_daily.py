"""Restart, pagination and partial-failure tests using real PCM WAV bytes."""

import hashlib
import io
import json
import wave
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.raw_archive.daily import Limits, Queue, run_daily
from app.raw_archive.policy import (
    ArchiveBlocked,
    Config,
    Ledger,
    Recording,
    RemoteMissing,
    archive_one,
)
from app.raw_archive.restore import archived_file
from app.raw_archive.runner import run
from app.raw_archive.worker import exclusive_worker

NOW = datetime.now(UTC)


def wav_bytes(seed):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(380)
        wav.writeframes(bytes([seed, 0]) * 760)
    return buffer.getvalue()


class Source:
    def __init__(self):
        self.data, self.failed, self.reads = {}, set(), []

    def snapshot(self, record):
        return {"VersionId": record.sha256}

    def download(self, record, snapshot, path):
        self.reads.append(record.key)
        if record.key in self.failed:
            raise OSError("simulated interrupted download")
        path.write_bytes(self.data[record.key])

    def evict(self, *_):
        pytest.fail("The daily worker must NEVER delete a source")


class Destination:
    identity = "sftp://isolated-test/archive"

    def __init__(self):
        self.data, self.writes, self.reads = {}, [], []

    def publish(self, path, key):
        self.writes.append(key)
        self.data[key] = path.read_bytes()

    def download(self, key, path, max_bytes):
        self.reads.append(key)
        if key not in self.data:
            raise RemoteMissing("proven missing in isolated fixture")
        path.write_bytes(self.data[key])


class Catalog:
    def __init__(self):
        self.records = {}

    def page(self, cursor, before, count):
        return [[key] for key in sorted(self.records) if not cursor or key > cursor[0]][:count]

    def resolve(self, reference):
        return self.records[reference[0]]

    def recent_page(self, cursor, after, before, count):
        keys = [
            key
            for key in sorted(self.records)
            if (not cursor or key > cursor[0])
            and any(after <= r.received_at < before for r in self.records[key])
        ]
        return [([key], [key]) for key in keys[:count]]

    def revalidate(self, recording):
        assert any(recording in rows for rows in self.records.values())


@pytest.fixture
def world(tmp_path):
    config = Config(enabled=True, root=tmp_path, min_free_bytes=0, max_files=100)
    catalogs = {kind: Catalog() for kind in ("gateway", "direct")}
    source, destination = Source(), Destination()
    sources = dict.fromkeys(catalogs, source)
    identities = {"gateway": "test-gateway-db", "direct": "test-direct-db"}

    def add(kind, number, *, reference=None):
        data = wav_bytes(number % 255)
        key = f"{kind}/{number:04d}.wav"
        bucket = "greenmind-raw" if kind == "gateway" else "greenmind-direct-staging-test"
        record = Recording(
            kind,
            str(number),
            bucket,
            key,
            hashlib.sha256(data).hexdigest(),
            len(data),
            NOW - timedelta(hours=1),
            NOW - timedelta(hours=1),
            "",
            NOW - timedelta(hours=1, seconds=2),
            "mac-14-c1-9f-d9-42-a4",
        )
        source.data[key] = data
        catalogs[kind].records.setdefault(reference or f"{number:04d}", []).append(record)
        return record

    def execute(*, cfg=config, limits=Limits(), now=NOW, healthy=lambda: True):
        return run_daily(
            cfg,
            catalogs,
            sources,
            destination,
            healthy=healthy,
            source_identities=identities,
            limits=limits,
            now=now,
        )

    return config, catalogs, source, destination, add, execute


def test_disabled_cli_needs_no_secrets_and_creates_no_state(tmp_path, monkeypatch):
    path = tmp_path / "never-created"
    monkeypatch.setenv("RAW_ARCHIVE_STATE_DIR", str(path))
    monkeypatch.setenv("RAW_ARCHIVE_ENABLED", "false")
    assert run() == {"status": "disabled", "transfers": 0, "deletions": 0}
    assert not path.exists()


def test_recent_wavs_backup_without_features_and_restore_while_worker_disabled(world):
    config, _, source, remote, add, execute = world
    record = add("gateway", 1)
    add("direct", 2)
    result = execute()
    assert result["status"] == "complete" and result["verified"] == 2
    with archived_file(
        replace(config, enabled=False),
        remote,
        kind=record.kind,
        bucket=record.bucket,
        key=record.key,
    ) as restored:
        assert restored.read_bytes() == source.data[record.key]
        with wave.open(str(restored)) as wav:
            assert wav.getnframes() == 760 and wav.getframerate() == 380
    assert not restored.exists()
    again = execute(now=NOW + timedelta(days=1))
    assert again["verified"] == 0 and again["reused"] == 2
    assert len(remote.writes) == 2


def test_paged_discovery_eventually_copies_every_file_and_late_insert(world):
    config, _, source, remote, add, execute = world
    for index in range(1, 25):
        add("gateway" if index % 2 else "direct", index)
    limits = Limits(references=8, page_size=2)
    for _ in range(18):
        execute(cfg=replace(config, max_files=3), limits=limits)
    assert len(remote.data) == 24 and len(source.reads) == 24
    add("gateway", 0)  # Inserted below a cursor must be found on the next sweep.
    for _ in range(8):
        execute(cfg=replace(config, max_files=3), limits=limits)
    assert len(remote.data) == 25


def test_revision_runs_continue_after_file_budget_not_truncated(world):
    config, _, source, remote, add, execute = world
    for index in range(1, 12):
        add("direct", index, reference="one-sealed-revision")
    for _ in range(5):
        result = execute(cfg=replace(config, max_files=3))
    assert result["status"] == "complete"
    assert len(remote.data) == 11 and len(source.reads) == 11


def test_failed_file_does_not_hide_other_files_and_retries(world):
    _, _, source, remote, add, execute = world
    bad = add("gateway", 1)
    add("gateway", 2)
    source.failed.add(bad.key)
    result = execute()
    assert result["status"] == "incomplete" and result["failed"] == result["pending"] == 1
    assert len(remote.data) == 1
    source.failed.clear()
    result = execute(now=NOW + timedelta(days=1))
    assert result["status"] == "complete" and len(remote.data) == 2
    assert source.reads.count(bad.key) == 2


def test_byte_budget_counts_failed_transfers(world):
    _, _, source, remote, add, execute = world
    first = add("gateway", 1)
    add("gateway", 2)
    source.failed.add(first.key)
    result = execute(limits=Limits(bytes=first.size))
    assert result["status"] == "incomplete" and result["budget_reached"]
    assert result["bytes"] == first.size and len(source.reads) == 1 and not remote.data


def test_cross_environment_and_destination_receipts_cannot_be_reused(world):
    config, _, _, remote, add, execute = world
    add("gateway", 1)
    execute()
    result = execute(cfg=replace(config, namespace="staging"))
    assert result["status"] == "blocked"
    assert "different sources/environment" in result["reason"]
    remote.identity += "/other"
    assert execute()["status"] == "blocked"
    assert len(remote.writes) == 1


def test_weekly_recheck_detects_remote_corruption(world):
    _, _, _, remote, add, execute = world
    add("gateway", 1)
    execute()
    key = next(iter(remote.data))
    remote.data[key] = b"corrupt"
    result = execute(now=NOW + timedelta(days=8))
    assert result["status"] == "incomplete" and result["failed"] == 1
    assert len(remote.writes) == 1  # Never silently replace a damaged archive.


def test_pause_and_low_headroom_stop_without_transfer(world):
    config, _, source, remote, add, execute = world
    add("gateway", 1)
    assert execute(healthy=lambda: False)["status"] == "blocked"
    (config.root / "PAUSE").touch()
    assert execute()["status"] == "blocked"
    assert not source.reads and not remote.writes


def test_daily_rejects_deletion_flag_before_transfers(world):
    config, _, source, _, add, execute = world
    add("gateway", 1)
    with pytest.raises(ArchiveBlocked, match="independent current feature"):
        execute(cfg=replace(config, delete_enabled=True, reads_accepted=True))
    assert not source.reads


def test_exclusive_process_lock(world):
    config, _, source, _, add, execute = world
    add("gateway", 1)
    with exclusive_worker(config), pytest.raises(ArchiveBlocked, match="Another archive"):
        execute()
    assert not source.reads


def test_missing_checksum_and_oversize_are_reported_not_filtered(world):
    config, catalogs, _, remote, add, execute = world
    record = add("gateway", 1)
    catalogs["gateway"].records["0001"] = [replace(record, sha256=None)]
    result = execute()
    assert result["failed"] == result["pending"] == 1
    catalogs["gateway"].records["0001"] = [replace(record, size=config.max_file_bytes + 1)]
    result = execute()
    assert result["status"] == "incomplete" and not remote.writes


def test_completed_receipt_refresh_is_persisted_and_features_may_arrive_later(world):
    config, catalogs, source, remote, add, execute = world
    record = add("gateway", 1)
    execute()
    record = replace(record, feature_digest="a" * 64)
    catalogs["gateway"].records["0001"] = [record]
    ledger = Ledger(config.root)
    try:
        later = NOW + timedelta(days=1)
        assert (
            archive_one(
                config,
                record,
                source,
                remote,
                ledger,
                catalogs["gateway"].revalidate,
                copy_only=True,
                now=lambda: later,
            )
            == "verified"
        )
        assert ledger.load(record)[1]["verified_at"] == later.isoformat()
        assert ledger.load(record)[1]["recording"]["feature_digest"] == "a" * 64
    finally:
        ledger.close()


def test_last_report_survives_restart(world):
    config, _, _, _, add, execute = world
    add("gateway", 1)
    result = execute()
    ledger = Ledger(config.root)
    try:
        persisted = ledger.db.execute(
            "SELECT value FROM backup_meta WHERE name='last_report'"
        ).fetchone()[0]
        assert json.loads(persisted) == result
    finally:
        ledger.close()


def test_missing_backup_is_repaired_only_from_verified_local_original(world):
    _, _, source, remote, add, execute = world
    record = add("gateway", 1)
    execute()
    remote.data.clear()
    result = execute(now=NOW + timedelta(days=8))
    assert result["status"] == "complete" and result["verified"] == 1
    assert len(remote.data) == 1 and source.reads.count(record.key) == 2


def test_repeated_failures_stop_work_and_preserve_retry_queue(world):
    _, _, source, _, add, execute = world
    for number in range(1, 9):
        source.failed.add(add("gateway", number).key)
    result = execute()
    assert result["failed"] == 3 and result["pending"] == 8
    assert len(source.reads) == 3 and result["status"] == "incomplete"
    source.failed.clear()
    assert execute()["status"] == "complete"


def test_health_failure_after_upload_leaves_original_and_no_verified_receipt(world):
    config, catalogs, source, remote, add, _ = world
    record = add("gateway", 1)
    checks = []

    def checkpoint():
        checks.append(True)
        if len(checks) == 3:
            raise ArchiveBlocked("resource pressure during transfer")

    ledger = Ledger(config.root)
    try:
        with pytest.raises(ArchiveBlocked, match="resource pressure"):
            archive_one(
                config,
                record,
                source,
                remote,
                ledger,
                catalogs["gateway"].revalidate,
                copy_only=True,
                checkpoint=checkpoint,
            )
        assert ledger.load(record) is None and record.key in source.data
        assert len(remote.data) == 1
        assert (
            archive_one(
                config,
                record,
                source,
                remote,
                ledger,
                catalogs["gateway"].revalidate,
                copy_only=True,
            )
            == "verified"
        )
    finally:
        ledger.close()


def test_new_recording_is_prioritized_ahead_of_historical_backlog(world):
    config, catalogs, source, _, add, execute = world
    for number in range(1, 25):
        record = add("gateway", number)
        catalogs["gateway"].records[f"{number:04d}"] = [
            replace(
                record,
                received_at=NOW - timedelta(days=200),
                ended_at=NOW - timedelta(days=200),
                started_at=NOW - timedelta(days=200),
            )
        ]
    new = add("gateway", 99)
    result = execute(cfg=replace(config, max_files=1), limits=Limits(references=8, page_size=2))
    assert source.reads == [new.key]
    assert result["status"] == "incomplete"  # Old backlog must not masquerade as complete.


def test_pending_queue_cannot_starve_historical_recordings(tmp_path):
    ledger = Ledger(tmp_path)
    try:
        queue = Queue(ledger, {"namespace": "test"})
        queue.enqueue_page("gateway", [[f"old-{n}"] for n in range(8)], finished=True)
        queue.enqueue_page("direct", [[f"new-{n}"] for n in range(8)], finished=True, recent=True)
        first = [json.loads(ref)[0] for _, ref in queue.pending(4)]
        second = [json.loads(ref)[0] for _, ref in queue.pending(4)]
        assert first == ["new-0", "old-0", "new-1", "old-1"]
        assert second == first
        assert json.loads(queue.pending(1)[0][1])[0] == "new-0"
        assert json.loads(queue.pending(1)[0][1])[0] == "old-0"
        queue.attempted("gateway", json.dumps(["old-0"]), completed=True)
        assert "old-0" not in [json.loads(ref)[0] for _, ref in queue.pending(16)]
    finally:
        ledger.close()


def test_recent_direct_work_cannot_hide_gateway_work(tmp_path):
    ledger = Ledger(tmp_path)
    try:
        queue = Queue(ledger, {"namespace": "test"})
        queue.enqueue_page("direct", [[f"direct-{n}"] for n in range(100)],
                           finished=True, recent=True)
        queue.enqueue_page("gateway", [[f"gateway-{n}"] for n in range(100)],
                           finished=True)
        for _ in range(8):
            pair = [kind for kind, _ in queue.pending(2)]
            assert pair.count("gateway") == pair.count("direct") == 1
    finally:
        ledger.close()


def test_large_backlog_limits_catalog_scans_but_copies_pending(world):
    config, _, _, remote, add, execute = world
    for n in range(1, 5):
        add("gateway", n)
    # The backlog is durable, even when not all references resolve in this fixture.
    ledger = Ledger(config.root)
    try:
        queue = Queue(ledger, {"namespace": config.namespace, "destination": remote.identity,
                               "sources": {"gateway": "test-gateway-db", "direct": "test-direct-db"}})
        queue.enqueue_page("gateway", [[f"missing-{n}"] for n in range(10001)], finished=False)
    finally:
        ledger.close()
    result = execute(limits=Limits(references=10000, page_size=100))
    assert result["scanned"] <= 400
    assert result["verified"] >= 1
    assert result["pending"] >= 10000
