"""Exact-set rehearsal, crash recovery and consent deadlines; no real objects."""

import json
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.raw_archive.pilot import run_pilot
from app.raw_archive.policy import ArchiveBlocked

from . import test_deletion_gates

NOW = test_deletion_gates.NOW
guarded = test_deletion_gates.guarded


def run(world, config, **kwargs):
    source, destination = world[2], world[3]
    source.deletion_target = lambda *_: None if source.deleted else {"VersionId": "immutable-v1"}
    catalog = SimpleNamespace(revalidate=lambda _: None)
    return run_pilot(
        config,
        dict.fromkeys(("gateway", "direct"), source),
        dict.fromkeys(("gateway", "direct"), catalog),
        destination,
        healthy=lambda: True,
        clock=lambda: NOW,
        **kwargs,
    )


def test_full_rehearsal_uses_all_proofs_without_deleting(guarded):
    config = replace(guarded[-1](), delete_enabled=False, reads_accepted=False)
    result = run(guarded, config)
    assert result["status"] == "READY_FOR_PILOT_APPROVAL"
    assert result["deleted_files"] == 0 and not guarded[2].deleted
    assert guarded[4].load(guarded[1])[0] == "verified"


def test_unsigned_rehearsal_cannot_be_executed(guarded, monkeypatch):
    monkeypatch.setenv("RAW_ARCHIVE_WORKER_ROLE", "source")
    config = guarded[-1](lambda data: data.pop("authorization"))
    with pytest.raises(ArchiveBlocked, match="explicit human"):
        run(guarded, config, execute=True)
    assert not guarded[2].deleted


def test_approved_exact_set_executes_and_resumes(guarded, monkeypatch):
    monkeypatch.setenv("RAW_ARCHIVE_WORKER_ROLE", "source")
    config = guarded[-1]()
    assert run(guarded, config, execute=True)["deleted_files"] == 1
    result = run(guarded, config, execute=True)
    assert result["deleted_files"] == 0
    assert result["completed_files"] == 1 and not result["deletion_outcome_unknown"]


def test_archive_host_cannot_remove_source_objects(guarded, monkeypatch):
    monkeypatch.setenv("RAW_ARCHIVE_WORKER_ROLE", "archive")
    with pytest.raises(ArchiveBlocked, match="source-side"):
        run(guarded, guarded[-1](), execute=True)
    assert not guarded[2].deleted


def test_missing_grace_receipts_require_republication(guarded):
    destination = guarded[3]
    original = destination.download_recovery

    def old_index(key, path, limit, snapshot):
        original(key, path, limit, snapshot)
        data = json.loads(path.read_bytes())
        del data["receipts"]
        path.write_text(json.dumps(data))

    destination.download_recovery = old_index
    with pytest.raises(ArchiveBlocked, match="readback mismatch"):
        run(guarded, replace(guarded[-1](), delete_enabled=False))
    assert not guarded[2].deleted


def test_current_archive_corruption_blocks_execution(guarded, monkeypatch):
    monkeypatch.setenv("RAW_ARCHIVE_WORKER_ROLE", "source")
    guarded[3].corrupt = True
    with pytest.raises(ArchiveBlocked, match="Current Storage Box"):
        run(guarded, guarded[-1](), execute=True)
    assert not guarded[2].deleted


def test_failed_delete_remains_recoverable_and_reports_uncertainty(guarded, monkeypatch):
    monkeypatch.setenv("RAW_ARCHIVE_WORKER_ROLE", "source")

    def crash(*_):
        raise ConnectionError("lost response")

    guarded[2].evict = crash
    report = {}
    with pytest.raises(ConnectionError):
        run(guarded, guarded[-1](), execute=True, report=report)
    assert guarded[4].load(guarded[1])[0] == "deleting"
    assert report["deletion_outcome_unknown"]


def test_expired_during_snapshot_readback_retains_original(guarded):
    from app.raw_archive.policy import archive_one

    current = [NOW]
    original = guarded[3].download_snapshot

    def slow_read(*args):
        original(*args)
        current[0] += timedelta(hours=2)

    guarded[3].download_snapshot = slow_read
    with pytest.raises(ArchiveBlocked, match="expired"):
        archive_one(
            guarded[-1](),
            guarded[1],
            guarded[2],
            guarded[3],
            guarded[4],
            lambda _: None,
            now=lambda: current[0],
        )
    assert not guarded[2].deleted
    assert guarded[4].load(guarded[1])[0] == "verified"


def test_copy_only_receipt_exports_current_features_without_resetting_grace(guarded):
    from app.raw_archive.recovery import recovery_index

    config, record, _, destination, ledger, _ = guarded
    original = ledger.load(record)[1]
    copy_only = json.loads(json.dumps(original))
    copy_only["recording"]["feature_digest"] = ""
    ledger.save(record, "verified", copy_only)
    raw, _ = recovery_index([record], ledger, config.namespace, destination)
    exported = json.loads(raw)["receipts"][record.archive_id]
    assert exported["recording"]["feature_digest"] == record.feature_digest
    assert exported["first_verified_at"] == original["first_verified_at"]
    assert ledger.load(record)[1]["recording"]["feature_digest"] == ""
