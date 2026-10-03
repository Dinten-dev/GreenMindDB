"""Full guarded removal with fake sources; no live data or archive touched."""

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.raw_archive.deletion import eligible_after
from app.raw_archive.policy import ArchiveBlocked, Config, Ledger, Recording, archive_one
from app.raw_archive.recovery import recovery_index
from app.raw_archive.worker import configuration

from .test_policy import PAYLOAD, Destination, Source

NOW = datetime(2026, 10, 3, 10, tzinfo=UTC)


def test_documented_approval_environment_is_loaded(tmp_path, monkeypatch):
    path = tmp_path / "reviewed.json"
    monkeypatch.setenv("RAW_ARCHIVE_DELETION_APPROVAL_FILE", str(path))
    monkeypatch.setenv("RAW_ARCHIVE_DELETION_APPROVAL_SHA256", "a" * 64)
    config = configuration()
    assert config.deletion_approval_file == path
    assert config.deletion_approval_sha256 == "a" * 64


@pytest.fixture
def guarded(tmp_path):
    config = Config(enabled=True, root=tmp_path, min_free_bytes=0)
    old = NOW - timedelta(days=10)
    record = Recording(
        "gateway",
        "record",
        "greenmind-raw",
        "original.wav",
        hashlib.sha256(PAYLOAD).hexdigest(),
        len(PAYLOAD),
        old,
        old,
        "a" * 64,
        old,
        "mac-14-c1-9f-d9-42-a4",
    )
    source, destination = Source(), Destination()
    ledger = Ledger(tmp_path)
    archive_one(
        config,
        record,
        source,
        destination,
        ledger,
        lambda _: None,
        now=lambda: NOW - timedelta(days=8),
        copy_only=True,
    )
    index, digest = recovery_index([record], ledger, config.namespace, destination)
    destination.download_recovery = lambda key, path, limit, snapshot: path.write_bytes(index)
    destination.download_snapshot = lambda name, key, path, limit: path.write_bytes(PAYLOAD)
    catalog = json.dumps(
        {
            "schema": 1,
            "environment": "production",
            "destination": destination.identity,
            "created_at": (NOW - timedelta(minutes=10)).isoformat(),
            "files": [{"sha256": "a" * 64, "rows": 1} for _ in range(3)],
        }
    ).encode()
    catalog_ref = {
        "key": "production/catalog-backup/" + hashlib.sha256(catalog).hexdigest() + ".json",
        "sha256": hashlib.sha256(catalog).hexdigest(),
        "size": len(catalog),
    }
    proof = json.dumps(
        {
            "schema": 1,
            "environment": "production",
            "destination": destination.identity,
            "created_at": NOW.isoformat(),
            "deleted_files": 0,
            "catalog_manifest": catalog_ref,
            "restored": [
                {"kind": kind, "rows": 1, "sha256": "a" * 64}
                for kind in ("ledger", "gateway", "direct")
            ],
        }
    ).encode()
    proof_ref = {
        "key": "production/catalog-backup/" + hashlib.sha256(proof).hexdigest() + ".json",
        "sha256": hashlib.sha256(proof).hexdigest(),
        "size": len(proof),
    }
    destination.download_catalog = lambda key, path, limit, snapshot: path.write_bytes(
        proof if key == proof_ref["key"] else catalog
    )
    data = {
        "schema": 1,
        "environment": "production",
        "created_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        "destination": destination.identity,
        "candidates": json.loads(index)["candidates"],
        "catalog_manifest": catalog_ref,
        "catalog_restore": proof_ref,
        "snapshot": {"name": "daily-20261003", "created_at": NOW.isoformat()},
        "recovery_index": {
            "key": f"production/recovery/{digest}.json",
            "sha256": digest,
            "size": len(index),
        },
    }
    path = tmp_path / "reviewed.json"

    def approve(change=None):
        edited = json.loads(json.dumps(data))
        if change:
            change(edited)
        raw = json.dumps(edited).encode()
        path.write_bytes(raw)
        path.chmod(0o600)
        return replace(
            config,
            delete_enabled=True,
            reads_accepted=True,
            deletion_approval_file=path,
            deletion_approval_sha256=hashlib.sha256(raw).hexdigest(),
        )

    yield config, record, source, destination, ledger, approve
    ledger.close()


def execute(world, config):
    _, record, source, destination, ledger, _ = world
    return archive_one(config, record, source, destination, ledger, lambda _: None, now=lambda: NOW)


def test_all_proofs_required_and_passed_before_removal(guarded):
    config = guarded[-1]()
    assert execute(guarded, config) == "evicted"
    assert guarded[2].deleted
    assert guarded[4].load(guarded[1])[0] == "evicted"


def test_reverification_does_not_restart_grace(guarded):
    cfg, record, source, destination, ledger, _ = guarded
    first = ledger.load(record)[1]["first_verified_at"]
    execute(guarded, cfg)
    receipt = ledger.load(record)[1]
    assert receipt["first_verified_at"] == first
    assert eligible_after(record, receipt) == NOW - timedelta(days=1)
    assert receipt["verified_at"] == NOW.isoformat()


def test_no_manifest_no_deletion(guarded):
    with pytest.raises(ArchiveBlocked, match="manifest"):
        execute(guarded, replace(guarded[0], delete_enabled=True, reads_accepted=True))
    assert not guarded[2].deleted


@pytest.mark.parametrize(
    "damage",
    [
        "expired",
        "wrong_environment",
        "wrong_candidate",
        "old_snapshot",
        "wrong_index",
        "no_catalog_restore",
    ],
)
def test_bad_proof_retains_source(guarded, damage):
    def change(data):
        if damage == "expired":
            data["expires_at"] = NOW.isoformat()
        elif damage == "wrong_environment":
            data["environment"] = "staging"
        elif damage == "wrong_candidate":
            data["candidates"][guarded[1].archive_id]["sha256"] = "b" * 64
        elif damage == "old_snapshot":
            data["snapshot"]["created_at"] = (NOW - timedelta(days=9)).isoformat()
        elif damage == "wrong_index":
            data["recovery_index"]["sha256"] = "b" * 64
        else:
            del data["catalog_restore"]

    with pytest.raises(ArchiveBlocked):
        execute(guarded, guarded[-1](change))
    assert not guarded[2].deleted


def test_snapshot_wav_corruption_retains_source(guarded):
    guarded[3].download_snapshot = lambda name, key, path, limit: path.write_bytes(b"corrupt")
    with pytest.raises(ArchiveBlocked, match="Snapshot WAV"):
        execute(guarded, guarded[-1]())
    assert not guarded[2].deleted


def test_fresh_receipt_and_recent_recording_keep_local_reserve(guarded):
    cfg, record, _, _, ledger, approve = guarded
    state, receipt = ledger.load(record)
    receipt["first_verified_at"] = (NOW - timedelta(days=6)).isoformat()
    ledger.save(record, state, receipt)
    with pytest.raises(ArchiveBlocked, match="reserve"):
        execute(guarded, approve())
    assert not guarded[2].deleted


def test_changed_manifest_and_symlink_are_rejected(guarded):
    config = guarded[-1]()
    config.deletion_approval_file.write_text("{}")
    with pytest.raises(ArchiveBlocked, match="changed"):
        execute(guarded, config)
    target = config.deletion_approval_file.with_name("link.json")
    target.symlink_to(config.deletion_approval_file)
    with pytest.raises(ArchiveBlocked, match="manifest"):
        execute(guarded, replace(config, deletion_approval_file=target))
    assert not guarded[2].deleted


def test_quarantine_blocks_even_if_features_are_repaired(guarded, tmp_path):
    from dataclasses import replace

    from app.raw_archive.deletion import check_quarantine

    config, record, *_ = guarded
    path = tmp_path / "quarantine.json"
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "environment": "production",
                "excluded": [{"kind": record.kind, "identity": record.identity}],
            }
        )
    )
    path.chmod(0o600)
    config = replace(config, quarantine_file=path)
    with pytest.raises(ArchiveBlocked, match="quarantined"):
        check_quarantine(config, record)
    path.write_text("corrupt")
    with pytest.raises(ArchiveBlocked, match="invalid"):
        check_quarantine(config, record)
    path.unlink()
    with pytest.raises(ArchiveBlocked, match="unavailable"):
        check_quarantine(config, record)
