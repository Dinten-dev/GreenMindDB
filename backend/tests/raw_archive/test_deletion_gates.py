"""Full guarded removal with fake sources; no live data or archive touched."""

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.raw_archive.deletion import eligible_after
from app.raw_archive.policy import ArchiveBlocked, Config, Ledger, Recording, archive_one
from app.raw_archive.readiness import REQUIRED_TESTS
from app.raw_archive.recovery import recovery_index
from app.raw_archive.wav_catalog import ALLOWLIST_SHA256, COLUMNS, FORMAT
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
    tables = {kind: dict.fromkeys(names, 0) for kind, names in COLUMNS.items()}
    tables["gateway"]["wav_file"] = 1
    tables["direct"]["direct_revision"] = 1
    files = [
        {
            "kind": kind,
            "file": kind + "-000000.jsonl.gz",
            "sha256": "a" * 64,
            "rows": 1,
            "bytes": 100,
            "decoded_bytes": 100,
        }
        for kind in ("ledger", "gateway", "direct")
    ]
    catalog = json.dumps(
        {
            "schema": 2,
            "format": FORMAT,
            "allowlist_sha256": ALLOWLIST_SHA256,
            "tables": tables,
            "environment": "production",
            "destination": destination.identity,
            "created_at": (NOW - timedelta(minutes=10)).isoformat(),
            "files": files,
        }
    ).encode()
    catalog_ref = {
        "key": "production/catalog-backup/" + hashlib.sha256(catalog).hexdigest() + ".json",
        "sha256": hashlib.sha256(catalog).hexdigest(),
        "size": len(catalog),
    }
    proof = json.dumps(
        {
            "schema": 2,
            "format": FORMAT,
            "allowlist_sha256": ALLOWLIST_SHA256,
            "tables": tables,
            "environment": "production",
            "destination": destination.identity,
            "created_at": NOW.isoformat(),
            "deleted_files": 0,
            "catalog_manifest": catalog_ref,
            "restored": [
                {"kind": kind, "rows": 1, "sha256": "a" * 64, "file": kind + "-000000.jsonl.gz"}
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
        "schema": 2,
        "environment": "production",
        "created_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        "destination": destination.identity,
        "candidates": json.loads(index)["candidates"],
        "catalog_manifest": catalog_ref,
        "catalog_restore": proof_ref,
        "snapshot": {
            "name": "daily-20261003",
            "created_at": NOW.isoformat(),
            "provider_id": 123,
            "storage_box_id": 456,
            "provider_verified": True,
        },
        "recovery_index": {
            "key": f"production/recovery/{digest}.json",
            "sha256": digest,
            "size": len(index),
        },
    }
    data["release_acceptance"] = {
        "schema": 2,
        "environment": "production",
        "destination": destination.identity,
        "revision": "a" * 40,
        "checked_at": NOW.isoformat(),
        "deployed_at": (NOW - timedelta(days=2)).isoformat(),
        "reader_live": True,
        "catalog_manifest": catalog_ref,
        "snapshot": data["snapshot"],
        "recovery_index": data["recovery_index"],
        "tests": dict.fromkeys(REQUIRED_TESTS, True),
        "evidence_sha256": dict.fromkeys(
            ("downloads", "observation", "reconciliation", "diagnostic", "provider"), "a" * 64
        ),
        "observation": {
            "receiver_observation_passed": True,
            "samples": 289,
            "elapsed_seconds": 86400,
            "maximum_gap_seconds": 310,
            "errors": 0,
            "protected_changes": 0,
            "unhealthy_samples": 0,
            "source_progress": {"gateway": True, "direct": True},
            "started_at": (NOW - timedelta(days=1)).isoformat(),
            "finished_at": NOW.isoformat(),
        },
        "reconciliation": {
            "complete": True,
            "eligible_pending_files": 0,
            "receipt_mismatches": 0,
            "unknown_eligible_files": 0,
            "inventory_sha256": "a" * 64,
            "cutoff": NOW.isoformat(),
        },
        "buckets": {
            record.bucket: {
                "versioning": "Enabled",
                "enabled_lifecycle_rules": 0,
                "checked_at": NOW.isoformat(),
            }
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


@pytest.mark.parametrize(
    "gate",
    [
        "reader_live",
        "tests",
        "observation",
        "reconciliation",
        "buckets",
        "evidence_sha256",
        "snapshot",
        "revision",
    ],
)
def test_missing_release_gate_never_removes_original(guarded, gate):
    def change(data):
        del data["release_acceptance"][gate]

    with pytest.raises(ArchiveBlocked):
        execute(guarded, guarded[-1](change))
    assert not guarded[2].deleted


def test_old_full_catalog_approval_is_rejected(guarded):
    with pytest.raises(ArchiveBlocked, match="schema"):
        execute(guarded, guarded[-1](lambda data: data.update(schema=1)))
    assert not guarded[2].deleted
