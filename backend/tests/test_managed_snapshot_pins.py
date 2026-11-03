import hashlib
import json
import threading
from datetime import UTC, datetime

import pytest

from app.raw_archive.coordination import lease
from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive.ledger import Ledger, initialize_snapshot_pins
from app.tiered_archive.pipeline import publish
from app.tiered_archive.registry_backup import backup, initialize_managed, restore
from tests.test_tiered_archive import AT, bundle  # noqa: F401

EPOCH = "a" * 32
INTENT = "b" * 32
PIN = "c" * 32
OWNER = {
    "host": "archive",
    "unit": "pilot.service",
    "invocation": "d" * 32,
    "pid_generation": "123:456",
}
SNAPSHOT = {"id": 22, "storage_box": 12, "name": "snapshot-22", "created": AT.isoformat()}


class Provider:
    box_id = 12

    def read(self, identity):
        return SNAPSHOT | {
            "description": "greenmind-managed-" + INTENT,
            "labels": {"purpose": "greenmind-managed", "intent": INTENT},
        }


@pytest.fixture
def subject(tmp_path, monkeypatch):
    coord = tmp_path / "coord"
    coord.mkdir(mode=0o700)
    monkeypatch.setenv("RAW_ARCHIVE_COORDINATION_DIR", str(coord))
    ledger = Ledger(tmp_path / "state")
    initialize_managed(ledger.db)
    initialize_snapshot_pins(ledger.db)
    with ledger.db:
        ledger.db.execute(
            "INSERT INTO managed_snapshot_scope VALUES(1,?,?,'current')", (EPOCH, "d" * 64)
        )
        ledger.db.execute(
            "INSERT INTO managed_snapshot VALUES(?,'verified','{}',?,'{}',?)",
            (INTENT, json.dumps(SNAPSHOT), AT.isoformat()),
        )
        ledger.db.execute("INSERT INTO managed_snapshot_epoch VALUES(?,?)", (INTENT, EPOCH))
    input_manifest = tmp_path / "input.json"
    input_manifest.write_text(
        json.dumps(
            {"schema": 1, "files": [{"sha256": hashlib.sha256(b"abc").hexdigest(), "bytes": 3}]}
        )
    )
    input_manifest.chmod(0o600)
    operation = tmp_path / "operation.json"
    operation.write_text(
        json.dumps(
            {
                "pin_id": PIN,
                "snapshot_id": 22,
                "kind": "restoreinput",
                "consumer_entrypoint": "raw-restore",
                "owner": OWNER,
                "scope_epoch": EPOCH,
                "input_sha256": hashlib.sha256(input_manifest.read_bytes()).hexdigest(),
                "input_manifest_path": str(input_manifest),
                "target_sha256": "f" * 64,
                "restore_plan_sha256": "1" * 64,
            }
        )
    )
    operation.chmod(0o600)
    digest = hashlib.sha256(operation.read_bytes()).hexdigest()
    yield ledger, operation, digest, tmp_path
    ledger.close()


def acquire(subject):
    ledger, operation, digest, _ = subject
    return ledger.acquire_snapshot_pin(
        pin_id=PIN,
        snapshot_id=22,
        kind="restoreinput",
        operation_path=operation,
        operation_sha256=digest,
        owner=OWNER,
        provider=Provider(),
        now=datetime.now(UTC),
    )


def test_pin_needs_live_existing_lock_and_survives_restart(subject):
    ledger, _, _, _ = subject
    with pytest.raises(ArchiveBlocked, match="jobs lock"):
        acquire(subject)
    with lease("copy"):
        assert acquire(subject) == PIN
        assert acquire(subject) == PIN
        assert ledger.db.execute("SELECT count(*) FROM managed_snapshot_pin_event").fetchone() == (
            1,
        )
    assert ledger.db.execute(
        "SELECT state FROM managed_snapshot_pin WHERE id=?", (PIN,)
    ).fetchone() == ("active",)
    with lease("copy"), ledger.db:
        ledger.db.execute("UPDATE managed_snapshot SET state='delete_intent'")
        with pytest.raises(ArchiveBlocked, match="Verified managed snapshot"):
            acquire(subject)


def test_root_operation_reconciliation_requires_durable_receipt_and_exact_journal(subject):
    from app.tiered_archive.managed_snapshot import reconcile_root_operations

    ledger, _, digest, _ = subject
    empty = {"root_operation_journals": [], "root_operations": []}
    with lease("copy"):
        reconcile_root_operations(ledger, epoch=EPOCH, current_ids=[22], rosters=[empty, empty])
        acquire(subject)
        with pytest.raises(ArchiveBlocked, match="receipt unavailable"):
            reconcile_root_operations(ledger, epoch=EPOCH, current_ids=[22], rosters=[empty, empty])
        receipt = {
            "state": "active",
            "scope_epoch": EPOCH,
            "pin_id": PIN,
            "snapshot_id": 22,
            "operation_sha256": digest,
            "owner": OWNER,
        }
        encoded = json.dumps(receipt, sort_keys=True)
        with ledger.db:
            ledger.db.execute(
                "INSERT INTO managed_snapshot_pin_receipt VALUES(?,?,?)",
                (PIN, encoded, hashlib.sha256((encoded + "\n").encode()).hexdigest()),
            )
        operation = {
            "pin_id": PIN,
            "snapshot_id": 22,
            "scope_epoch": EPOCH,
            "operation_sha256": digest,
            "owner": OWNER,
            "state": "active",
        }
        journal = {"path": "/root/operations", "device": 1, "inode": 2, "operations": [operation]}
        projection = {
            k: operation[k] for k in ("pin_id", "snapshot_id", "operation_sha256", "owner", "state")
        } | {
            "journal_path": journal["path"],
            "journal_device": 1,
            "journal_inode": 2,
        }
        roster = {"root_operation_journals": [journal], "root_operations": [projection]}
        reconcile_root_operations(ledger, epoch=EPOCH, current_ids=[22], rosters=[roster, empty])
        with pytest.raises(ArchiveBlocked, match="unique issued pin"):
            reconcile_root_operations(
                ledger, epoch=EPOCH, current_ids=[22], rosters=[roster, roster]
            )
        with pytest.raises(ArchiveBlocked, match="lacks matching root operation"):
            reconcile_root_operations(ledger, epoch=EPOCH, current_ids=[22], rosters=[empty, empty])
        operation["state"] = "complete"
        roster["root_operations"] = []
        with pytest.raises(ArchiveBlocked, match="lacks matching root operation"):
            reconcile_root_operations(
                ledger, epoch=EPOCH, current_ids=[22], rosters=[roster, empty]
            )
        reconcile_root_operations(
            ledger, epoch=EPOCH, current_ids=[22], rosters=[roster, empty], terminating_pin=PIN
        )
        operation["state"] = "expired"
        with pytest.raises(ArchiveBlocked, match="lacks matching root operation"):
            reconcile_root_operations(
                ledger, epoch=EPOCH, current_ids=[22], rosters=[roster, empty], terminating_pin=PIN
            )
        operation["state"] = "active"
        roster["root_operations"] = []
        with pytest.raises(ArchiveBlocked, match="projection differs"):
            reconcile_root_operations(
                ledger, epoch=EPOCH, current_ids=[22], rosters=[roster, empty]
            )


def test_old_catalog_cannot_hide_newer_current_epoch_operations(subject):
    from app.tiered_archive.managed_snapshot import reconcile_root_operations

    ledger, _, digest, _ = subject
    operation = {
        "pin_id": PIN,
        "snapshot_id": 99,
        "scope_epoch": EPOCH,
        "operation_sha256": digest,
        "owner": OWNER,
        "state": "active",
    }
    journal = {"path": "/root/operations", "device": 1, "inode": 2, "operations": [operation]}
    roster = {"root_operation_journals": [journal], "root_operations": []}
    empty = {"root_operation_journals": [], "root_operations": []}
    with lease("copy"):
        for state in ("active", "uncertain", "complete", "expired"):
            operation["state"] = state
            with pytest.raises(ArchiveBlocked, match="missing from snapshot inventory"):
                reconcile_root_operations(
                    ledger, epoch=EPOCH, current_ids=[22], rosters=[roster, empty]
                )
        # Historical manual or other-epoch operations are not managed victims.
        operation["scope_epoch"] = "e" * 32
        reconcile_root_operations(ledger, epoch=EPOCH, current_ids=[22], rosters=[roster, empty])


def test_current_epoch_pin_missing_from_inventory_blocks_reconciliation(subject):
    from app.tiered_archive.managed_snapshot import reconcile_root_operations

    ledger, _, _, _ = subject
    empty = {"root_operation_journals": [], "root_operations": []}
    with lease("copy"):
        acquire(subject)
        with pytest.raises(ArchiveBlocked, match="pin is missing"):
            reconcile_root_operations(ledger, epoch=EPOCH, current_ids=[], rosters=[empty, empty])


def test_completed_operation_for_proven_deleted_snapshot_is_historical(subject):
    from app.tiered_archive.managed_snapshot import reconcile_root_operations

    ledger, _, digest, _ = subject
    old_intent = "e" * 32
    operation = {
        "pin_id": PIN,
        "snapshot_id": 99,
        "scope_epoch": EPOCH,
        "operation_sha256": digest,
        "owner": OWNER,
        "state": "complete",
    }
    journal = {"path": "/root/operations", "device": 1, "inode": 2, "operations": [operation]}
    roster = {"root_operation_journals": [journal], "root_operations": []}
    empty = {"root_operation_journals": [], "root_operations": []}
    with ledger.db:
        ledger.db.execute(
            "INSERT INTO managed_snapshot VALUES(?,'deleted','{}',?,'{}',?)",
            (old_intent, json.dumps(SNAPSHOT | {"id": 99}), AT.isoformat()),
        )
        ledger.db.execute("INSERT INTO managed_snapshot_epoch VALUES(?,?)", (old_intent, EPOCH))
        ledger.db.execute(
            "INSERT INTO managed_snapshot_pin VALUES(?,?,?,?,?,?,'released',?,?,?)",
            (
                PIN,
                old_intent,
                99,
                "restoreinput",
                digest,
                json.dumps(OWNER, sort_keys=True, separators=(",", ":")),
                AT.isoformat(),
                AT.isoformat(),
                "f" * 64,
            ),
        )
    with lease("copy"):
        reconcile_root_operations(ledger, epoch=EPOCH, current_ids=[22], rosters=[roster, empty])
        operation["state"] = "active"
        with pytest.raises(ArchiveBlocked, match="missing from snapshot inventory"):
            reconcile_root_operations(
                ledger, epoch=EPOCH, current_ids=[22], rosters=[roster, empty]
            )


def test_roster_frame_binds_collected_runtime_and_format(subject, monkeypatch):
    from app.tiered_archive import managed_snapshot

    _, _, _, root = subject
    collector_path = root / "collector.py"
    collector_path.write_bytes(b"reviewed collector")
    collector_path.chmod(0o400)
    collector_sha = hashlib.sha256(collector_path.read_bytes()).hexdigest()
    monkeypatch.setattr(managed_snapshot, "_REVIEWED_ROSTER_COLLECTORS", {collector_sha})
    machine = "3" * 64
    boot = "12345678-1234-1234-1234-123456789abc"
    plan = {
        "format": "greenmind.managed-snapshot.root-roster-plan.v1",
        "role": "source",
        "hostname": "source",
        "machine_sha256": machine,
        "boot_id": boot,
        "scope_epoch": EPOCH,
        "collector_sha256": collector_sha,
        "new_snapshot_ids": [22],
        "units": [],
        "journals": [],
        "runtime": {"runtime_sha256": "4" * 64},
    }
    roster = {
        "format": "greenmind.managed-snapshot.consumer-roster.v1",
        "status": "PASS",
        "reasons": [],
        "unknown_units": [],
        "missing_units": [],
        "plan_sha256": "",
        "scope_epoch": EPOCH,
        "role": "source",
        "hostname": "source",
        "machine_sha256": machine,
        "boot_id": boot,
        "observed_at": datetime.now(UTC).isoformat(),
        "collector_sha256": collector_sha,
        "runtime_sha256": "4" * 64,
        "source_lease_profile": {"sha256": "5" * 64},
        "units": [],
        "root_operations": [],
        "root_operation_journals": [],
        "legacy_latest_snapshot_consumers": [],
    }
    plan_path, roster_path = root / "plan.json", root / "roster.json"

    def write_private(path, value):
        path.chmod(0o600) if path.exists() else None
        path.write_text(json.dumps(value))
        path.chmod(0o400)
        return hashlib.sha256(path.read_bytes()).hexdigest()

    plan_sha = write_private(plan_path, plan)
    roster["plan_sha256"] = plan_sha
    binding = {
        "roster_path": str(roster_path),
        "roster_sha256": write_private(roster_path, roster),
        "plan_path": str(plan_path),
        "plan_sha256": plan_sha,
        "collector_path": str(collector_path),
        "collector_sha256": collector_sha,
        "hostname": "source",
        "machine_sha256": machine,
        "boot_id": boot,
    }
    source = {"host": "source", "machine_sha256": machine, "profile_sha256": "5" * 64}

    def validate():
        return managed_snapshot.validate_roster_frame(
            binding, role="source", epoch=EPOCH, current_ids=[22], scope_source=source
        )

    validate()
    roster["runtime_sha256"] = "6" * 64
    binding["roster_sha256"] = write_private(roster_path, roster)
    with pytest.raises(ArchiveBlocked, match="identity, epoch or complete inventory"):
        validate()
    roster["runtime_sha256"] = "4" * 64
    roster["format"] = "greenmind.managed-snapshot.consumer-roster.v2"
    binding["roster_sha256"] = write_private(roster_path, roster)
    with pytest.raises(ArchiveBlocked, match="identity, epoch or complete inventory"):
        validate()


def test_unit_exclusion_requires_exact_reviewed_execution_and_profile(subject):
    from app.tiered_archive.managed_snapshot import _json, validate_roster_attestations

    ledger, _, _, root = subject
    unit_plan = {
        "name": "sensor.service",
        "execstart_sha256": "1" * 64,
        "modules": [],
        "environment_files": [{"sha256": "2" * 64}],
    }
    digest = hashlib.sha256(_json(unit_plan).encode()).hexdigest()
    review = {
        "format": "greenmind.managed-snapshot.unit-exclusion.v1",
        "scope_epoch": EPOCH,
        "role": "source",
        "unit": "sensor.service",
        "unit_plan_sha256": digest,
        "reason": "sensor-receiver-no-snapshot",
    }
    review_path = root / "review.json"
    review_path.write_text(json.dumps(review))
    review_path.chmod(0o400)
    policy = {
        "name": "sensor.service",
        "mode": "excluded",
        "plan_sha256": digest,
        "review_path": str(review_path),
        "review_sha256": hashlib.sha256(review_path.read_bytes()).hexdigest(),
    }
    roster = {
        "scope_epoch": EPOCH,
        "role": "source",
        "units": [{"name": "sensor.service"}],
        "reasons": ["loaded_module_binding_unproven"],
    }
    plan = {"units": [unit_plan]}
    validate_roster_attestations(ledger, roster=roster, plan=plan, policies=[policy], evidence=[])
    with pytest.raises(ArchiveBlocked, match="coverage differs"):
        validate_roster_attestations(ledger, roster=roster, plan=plan, policies=[], evidence=[])
    unit_plan["execstart_sha256"] = "3" * 64
    with pytest.raises(ArchiveBlocked, match="execution or profile differs"):
        validate_roster_attestations(
            ledger, roster=roster, plan=plan, policies=[policy], evidence=[]
        )


def test_inactive_pilot_requires_exact_reviewed_launcher(subject):
    from app.tiered_archive import managed_snapshot

    ledger, _, _, _ = subject
    unit_plan = {
        "name": "pilot.service",
        "entrypoint": "/root/launcher.py",
        "modules": [
            {"path": "/root/launcher.py", "sha256": managed_snapshot._REVIEWED_PILOT_LAUNCHER}
        ],
    }
    policy = {
        "name": "pilot.service",
        "mode": "live",
        "launcher_sha256": managed_snapshot._REVIEWED_PILOT_LAUNCHER,
        "plan_sha256": hashlib.sha256(managed_snapshot._json(unit_plan).encode()).hexdigest(),
    }
    unit = {
        "name": "pilot.service",
        "active_state": "inactive",
        "main_pid": 0,
        "main_start_ticks": 0,
        "invocation": "",
    }
    roster = {"scope_epoch": EPOCH, "role": "source", "units": [unit], "reasons": []}
    plan = {"units": [unit_plan]}
    managed_snapshot.validate_roster_attestations(
        ledger, roster=roster, plan=plan, policies=[policy], evidence=[]
    )
    unit["active_state"], unit["main_pid"] = "active", 123
    with pytest.raises(ArchiveBlocked, match="Inactive snapshot unit"):
        managed_snapshot.validate_roster_attestations(
            ledger, roster=roster, plan=plan, policies=[policy], evidence=[]
        )


def test_competing_acquire_cannot_overtake_jobs_lock(subject):
    from app.raw_archive.health import SafetyPause

    result = []

    def contender():
        try:
            with lease("copy"):
                result.append("acquired")
        except SafetyPause:
            result.append("blocked")

    with lease("copy"):
        thread = threading.Thread(target=contender)
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert result == ["blocked"]


def test_production_creation_requires_scoped_epoch_before_provider_io(subject):
    from app.tiered_archive.managed_snapshot import cycle

    ledger, _, _, _ = subject
    with ledger.db:
        ledger.db.execute("UPDATE managed_snapshot_scope SET recovery_state='needs_reconcile'")

    class NoProvider:
        box_id = 12

        def inventory(self):
            pytest.fail("Provider access must not start")

    with (
        lease("copy"),
        pytest.raises(ArchiveBlocked, match="Current pinned managed snapshot scope"),
    ):
        cycle(ledger, None, NoProvider(), lambda: None, enforce_scope=True)


def test_actual_guard_blocks_active_pin_under_existing_lock(subject):
    from app.tiered_archive.managed_snapshot import check_external_dependencies

    ledger, _, _, _ = subject
    with lease("copy"):
        acquire(subject)
        with pytest.raises(ArchiveBlocked, match="active or uncertain pins"):
            check_external_dependencies(SNAPSHOT, [], ledger=ledger, provider=Provider())


def test_matching_local_roster_does_not_forge_live_source_proof(subject, monkeypatch):
    from pathlib import Path

    from app.tiered_archive import managed_snapshot, runtime

    ledger, _, _, root = subject
    monkeypatch.setattr(runtime, "runtime_sha256", lambda: "7" * 64)
    app_root = Path(managed_snapshot.__file__).resolve().parents[1]
    paths = {
        "raw-storage": "raw_archive/storage.py",
        "raw-pilot": "raw_archive/pilot.py",
        "raw-recovery": "raw_archive/recovery.py",
        "raw-catalog": "raw_archive/wav_catalog.py",
        "snapshot-api": "raw_archive/snapshot_api.py",
        "tiered-storage": "tiered_archive/storage.py",
        "tiered-proof": "tiered_archive/pipeline.py",
    }
    consumers = [
        {
            "name": name,
            "path": str(app_root / path),
            "sha256": hashlib.sha256((app_root / path).read_bytes()).hexdigest(),
        }
        for name, path in paths.items()
    ]

    def binding(path):
        info = path.stat()
        return {
            "path": str(path),
            "device": info.st_dev,
            "inode": info.st_ino,
            "owner": info.st_uid,
            "mode": info.st_mode & 0o7777,
        }

    with lease("copy"):
        contract = {
            "schema": 2,
            "environment": "production",
            "box_id": 12,
            "epoch": EPOCH,
            "journal": binding(ledger.root / "tiered.sqlite3"),
            "coordination": binding(root / "coord" / "jobs.lock"),
            "source_lease": {},
            "raw_ledgers": [],
            "pilot_manifests": [],
            "restore_descriptors": [],
            "consumers": consumers,
            "source_roster_evidence": {},
            "archive_roster_evidence": {},
            "runtime_sha256": "7" * 64,
        }
        with pytest.raises(ArchiveBlocked, match="Live source catalogue lease"):
            managed_snapshot.validate_local_scope_bindings(contract, ledger, Provider())
        contract["consumers"][0]["sha256"] = "0" * 64
        with pytest.raises(ArchiveBlocked, match="consumer code differs"):
            managed_snapshot.validate_local_scope_bindings(contract, ledger, Provider())


@pytest.mark.parametrize(
    "executor",
    [
        "app.tiered_archive.database_restore.restore_backup",
        "app.raw_archive.recovery.restore_bundle",
    ],
)
def test_frozen_release_requires_exact_read_only_complete_inputs(subject, monkeypatch, executor):
    from app.tiered_archive import runtime

    monkeypatch.setattr(runtime, "runtime_sha256", lambda: "2" * 64)
    ledger, operation, digest, root = subject
    artifact = root / "data"
    artifact.write_bytes(b"abc")
    artifact.chmod(0o400)
    manifest = root / "frozen.json"
    data = {
        "schema": 1,
        "pin_id": PIN,
        "snapshot_id": 22,
        "operation_sha256": digest,
        "local_only": True,
        "target_sha256": "f" * 64,
        "restore_plan_sha256": "1" * 64,
        "local_runtime_sha256": "2" * 64,
        "local_executor": executor,
        "files": [
            {"path": str(artifact), "bytes": 3, "sha256": hashlib.sha256(b"abc").hexdigest()}
        ],
    }
    manifest.write_text(json.dumps(data))
    manifest.chmod(0o600)
    proof = hashlib.sha256(manifest.read_bytes()).hexdigest()
    with lease("copy"):
        acquire(subject)
        artifact.chmod(0o600)
        with pytest.raises(ArchiveBlocked, match="artifact identity"):
            ledger.release_frozen_snapshot_pin(
                pin_id=PIN,
                operation_path=operation,
                operation_sha256=digest,
                owner=OWNER,
                frozen_manifest=manifest,
                manifest_sha256=proof,
                replay_guard=lambda *_: None,
            )
        artifact.chmod(0o400)
        with pytest.raises(ArchiveBlocked, match="replay closure"):
            ledger.release_frozen_snapshot_pin(
                pin_id=PIN,
                operation_path=operation,
                operation_sha256=digest,
                owner=OWNER,
                frozen_manifest=manifest,
                manifest_sha256=proof,
            )
        ledger.release_frozen_snapshot_pin(
            pin_id=PIN,
            operation_path=operation,
            operation_sha256=digest,
            owner=OWNER,
            frozen_manifest=manifest,
            manifest_sha256=proof,
            replay_guard=lambda *_: None,
        )
    assert ledger.db.execute(
        "SELECT state FROM managed_snapshot_pin WHERE id=?", (PIN,)
    ).fetchone() == ("released",)


def test_catalog_roundtrip_preserves_pins_and_marks_reconciliation(subject, bundle, tmp_path):  # noqa: F811
    ledger, _, _, _ = subject
    candidate, path, metadata, _, box, _ = bundle
    publish(candidate, [(path, metadata)], box, ledger, checkpoint=lambda: None, now=AT)
    with lease("copy"):
        acquire(subject)
    receipt = {
        "schema": 1,
        "state": "active",
        "consumer_entrypoint": "raw-restore",
        "pin_id": PIN,
        "snapshot_id": 22,
        "snapshot_intent": INTENT,
        "kind": "restoreinput",
        "owner": OWNER,
        "operation_sha256": subject[2],
        "scope_epoch": EPOCH,
    }
    encoded = json.dumps(receipt, sort_keys=True)
    receipt_sha = hashlib.sha256((encoded + "\n").encode()).hexdigest()
    with ledger.db:
        ledger.db.execute(
            "INSERT INTO managed_snapshot_pin_receipt VALUES(?,?,?)", (PIN, encoded, receipt_sha)
        )
    reference = backup(ledger, box, checkpoint=lambda: None)
    target = Ledger(tmp_path / "restored")
    try:
        restore(reference, target, box, checkpoint=lambda: None, expected_environment="staging")
        assert target.db.execute(
            "SELECT state FROM managed_snapshot_pin WHERE id=?", (PIN,)
        ).fetchone() == ("active",)
        assert target.db.execute(
            "SELECT recovery_state FROM managed_snapshot_scope"
        ).fetchone() == ("needs_reconcile",)
        assert target.db.execute(
            "SELECT receipt_sha256 FROM managed_snapshot_pin_receipt"
        ).fetchone() == (receipt_sha,)
    finally:
        target.close()


def test_source_pilot_without_pin_stops_before_snapshot_io(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app.raw_archive import pilot

    config = SimpleNamespace(
        namespace="production",
        root=tmp_path,
        delete_enabled=False,
        guard=lambda: None,
        deletion_approval_sha256="e" * 64,
    )
    data = {"snapshot": {"provider_id": 22, "name": "snapshot-22"}}
    monkeypatch.setenv("RAW_ARCHIVE_MANAGED_SCOPE_REQUIRED", "true")
    monkeypatch.setattr(pilot, "proposal", lambda *_: data)
    monkeypatch.setattr(pilot, "load_index", lambda *_: pytest.fail("snapshot I/O started"))
    with pytest.raises(ArchiveBlocked, match="receipt and scope"):
        pilot.run_pilot(
            config,
            {"gateway": None, "direct": None},
            {"gateway": None, "direct": None},
            None,
            healthy=lambda: True,
        )


def test_source_receipt_rejects_different_process_generation(tmp_path, monkeypatch):
    from app.raw_archive import snapshot_api

    receipt = {
        "schema": 1,
        "state": "active",
        "kind": "raw_pilot",
        "consumer_entrypoint": "raw-pilot",
        "scope_epoch": EPOCH,
        "input_sha256": "e" * 64,
        "snapshot_id": 22,
        "snapshot_name": "snapshot-22",
        "pin_id": PIN,
        "snapshot_intent": INTENT,
        "owner": OWNER,
    }
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt))
    path.chmod(0o600)
    monkeypatch.setenv("RAW_ARCHIVE_MANAGED_PIN_RECEIPT_FILE", str(path))
    monkeypatch.setenv(
        "RAW_ARCHIVE_MANAGED_PIN_RECEIPT_SHA256", hashlib.sha256(path.read_bytes()).hexdigest()
    )
    monkeypatch.setenv("RAW_ARCHIVE_MANAGED_SCOPE_EPOCH", EPOCH)
    monkeypatch.setattr(
        snapshot_api, "pilot_process_owner", lambda: dict(OWNER, pid_generation="123:999")
    )
    with pytest.raises(ArchiveBlocked, match="does not bind"):
        snapshot_api.require_pilot_pin_receipt(
            {"snapshot": {"provider_id": 22, "name": "snapshot-22"}}, "e" * 64
        )
    monkeypatch.setattr(snapshot_api, "pilot_process_owner", lambda: OWNER)
    assert (
        snapshot_api.require_pilot_pin_receipt(
            {"snapshot": {"provider_id": 22, "name": "snapshot-22"}}, "e" * 64
        )
        == PIN
    )


def test_snapshot_transport_rejects_receipt_replay_or_wrong_operation(tmp_path, monkeypatch):
    from app.raw_archive import snapshot_api

    operation_sha = "9" * 64
    receipt = {
        "schema": 1,
        "state": "active",
        "scope_epoch": EPOCH,
        "snapshot_name": "snapshot-22",
        "operation_sha256": operation_sha,
        "pin_id": PIN,
        "snapshot_intent": INTENT,
        "owner": OWNER,
    }
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt))
    path.chmod(0o600)
    monkeypatch.setenv("RAW_ARCHIVE_MANAGED_SCOPE_REQUIRED", "true")
    monkeypatch.setenv("RAW_ARCHIVE_MANAGED_SCOPE_EPOCH", EPOCH)
    monkeypatch.setenv("RAW_ARCHIVE_MANAGED_PIN_RECEIPT_FILE", str(path))
    monkeypatch.setenv(
        "RAW_ARCHIVE_MANAGED_PIN_RECEIPT_SHA256", hashlib.sha256(path.read_bytes()).hexdigest()
    )
    monkeypatch.setenv("RAW_ARCHIVE_MANAGED_OPERATION_SHA256", operation_sha)
    monkeypatch.setattr(snapshot_api, "pilot_process_owner", lambda: OWNER)
    monkeypatch.setattr(snapshot_api, "capture_managed_import_proof", lambda _receipt: None)
    snapshot_api.require_managed_snapshot_access("snapshot-22")
    with pytest.raises(ArchiveBlocked, match="receipt differs"):
        snapshot_api.require_managed_snapshot_access("different")
    monkeypatch.setattr(
        snapshot_api, "pilot_process_owner", lambda: dict(OWNER, pid_generation="123:999")
    )
    with pytest.raises(ArchiveBlocked, match="receipt differs"):
        snapshot_api.require_managed_snapshot_access("snapshot-22")


def test_archive_snapshot_access_requires_exact_bound_jobs_lock(subject, monkeypatch):
    from app.raw_archive import snapshot_api

    _, _, _, root = subject
    monkeypatch.setenv("RAW_ARCHIVE_MANAGED_SCOPE_REQUIRED", "true")
    monkeypatch.setenv("RAW_ARCHIVE_WORKER_ROLE", "archive")
    contract = root / "contract.json"
    with lease("copy"):
        lock = root / "coord" / "jobs.lock"
        info = lock.stat()
        contract.write_text(
            json.dumps(
                {"coordination": {"path": str(lock), "device": info.st_dev, "inode": info.st_ino}}
            )
        )
        contract.chmod(0o600)
        monkeypatch.setenv("TIERED_ARCHIVE_SNAPSHOT_DEPENDENCIES_FILE", str(contract))
        monkeypatch.setenv(
            "TIERED_ARCHIVE_SNAPSHOT_DEPENDENCIES_SHA256",
            hashlib.sha256(contract.read_bytes()).hexdigest(),
        )
        snapshot_api.require_managed_snapshot_access("snapshot-22")
    with pytest.raises(ArchiveBlocked, match="pin required"):
        snapshot_api.require_managed_snapshot_access("snapshot-22")


def test_real_snapshot_transports_block_before_io_without_pin(tmp_path, monkeypatch):
    from app.raw_archive.storage import StorageBox
    from app.tiered_archive.storage import Store

    monkeypatch.setenv("RAW_ARCHIVE_MANAGED_SCOPE_REQUIRED", "true")
    monkeypatch.delenv("RAW_ARCHIVE_MANAGED_PIN_RECEIPT_FILE", raising=False)
    monkeypatch.delenv("RAW_ARCHIVE_WORKER_ROLE", raising=False)
    with pytest.raises(ArchiveBlocked, match="pin required"):
        StorageBox.download_snapshot(None, "snapshot-22", "ignored", tmp_path / "out", 10)
    with pytest.raises(ArchiveBlocked, match="pin required"):
        StorageBox.download_recovery(None, "ignored", tmp_path / "out", 10, snapshot="snapshot-22")
    with pytest.raises(ArchiveBlocked, match="pin required"):
        StorageBox.download_catalog(None, "ignored", tmp_path / "out", 10, snapshot="snapshot-22")
    with pytest.raises(ArchiveBlocked, match="pin required"):
        Store(None).download("ignored", tmp_path / "out", 10, snapshot="snapshot-22")
    with pytest.raises(ArchiveBlocked, match="pin required"):
        StorageBox._download_path(
            None, "/home/.zfs/snapshot/snapshot-22/object", tmp_path / "out", 10
        )


def test_pilot_owner_reads_actual_systemd_invocation_and_pid_start(monkeypatch):
    from app.raw_archive import snapshot_api

    monkeypatch.setenv("INVOCATION_ID", "d" * 32)
    monkeypatch.setenv("RAW_ARCHIVE_OPERATION_UNIT", "greenmind-pilot.service")
    monkeypatch.setattr(snapshot_api.os, "getpid", lambda: 123)

    def read_text(path):
        if str(path) == "/proc/self/stat":
            return "123 (pilot process) " + " ".join(["S"] + ["0"] * 18 + ["98765"])
        if str(path) == "/proc/self/cgroup":
            return "0::/system.slice/greenmind-pilot.service\n"
        raise OSError("unexpected")

    monkeypatch.setattr(snapshot_api.Path, "read_text", read_text)
    owner = snapshot_api.pilot_process_owner()
    assert owner["unit"] == "greenmind-pilot.service"
    assert owner["pid_generation"] == "123:98765"
    assert owner["invocation"] == "d" * 32
    monkeypatch.setenv("RAW_ARCHIVE_OPERATION_UNIT", "different.service")
    with pytest.raises(ArchiveBlocked, match="process generation"):
        snapshot_api.pilot_process_owner()


@pytest.mark.parametrize("kind", ["restoreinput", "raw_pilot"])
def test_owned_termination_requires_pinned_exact_generation(subject, kind):
    from app.tiered_archive.managed_snapshot import validate_owned_termination

    ledger, operation_path, digest, root = subject
    operation = json.loads(operation_path.read_text())
    if kind == "raw_pilot":
        operation["kind"] = kind
        operation["consumer_entrypoint"] = "raw-pilot"
        operation_path.write_text(json.dumps(operation))
        digest = hashlib.sha256(operation_path.read_bytes()).hexdigest()
    with lease("copy"):
        ledger.acquire_snapshot_pin(
            pin_id=PIN,
            snapshot_id=22,
            kind=kind,
            operation_path=operation_path,
            operation_sha256=digest,
            owner=OWNER,
            provider=Provider(),
        )

    def write(name, value):
        path = root / name
        body = value if isinstance(value, bytes) else json.dumps(value).encode()
        path.write_bytes(body)
        path.chmod(0o400)
        return path, hashlib.sha256(body).hexdigest()

    consumer = {
        "pid": 123,
        "start_ticks": 456,
        "unit": OWNER["unit"],
        "invocation": OWNER["invocation"],
        "cgroup": "/system.slice/pilot.service",
        "cgroup_device": 1,
        "cgroup_inode": 123,
    }
    receipt_body = {
        "schema": 1,
        "state": "active",
        "kind": kind,
        "consumer_entrypoint": operation["consumer_entrypoint"],
        "pin_id": PIN,
        "snapshot_id": 22,
        "scope_epoch": EPOCH,
        "operation_sha256": digest,
        "input_sha256": operation["input_sha256"],
        "owner": OWNER,
    }
    receipt_encoded = json.dumps(receipt_body, sort_keys=True)
    receipt_path, receipt_sha = write("receipt", (receipt_encoded + "\n").encode())
    with ledger.db:
        ledger.db.execute(
            "INSERT INTO managed_snapshot_pin_receipt VALUES(?,?,?)",
            (PIN, receipt_encoded, receipt_sha),
        )
    marker_path, marker_sha = write(
        "marker",
        {
            "format": "greenmind.managed-snapshot.marker.v1",
            "pin_id": PIN,
            "snapshot_id": 22,
            "scope_epoch": EPOCH,
            "operation_sha256": digest,
            "input_sha256": operation["input_sha256"],
            "owner": OWNER,
        },
    )
    startup_path, startup_sha = write(
        "startup", {"format": "greenmind.managed-snapshot.consumer-startup.v1", **consumer}
    )
    producer_path, producer_sha = write("producer", b"root-reviewed-producer")
    producer_scope_path, producer_scope_sha = write(
        "producer-scope",
        {
            "format": "greenmind.managed-snapshot.owned-proof-scope.v1",
            "receipt_path": "/source/receipt",
            "receipt_sha256": receipt_sha,
            "marker_path": "/source/marker",
            "marker_sha256": marker_sha,
            "operation_path": "/source/operation",
            "operation_sha256": digest,
            "startup_path": "/source/startup",
            "startup_sha256": startup_sha,
            "source_machine_sha256": "8" * 64,
            "source_hostname": OWNER["host"],
            "producer_sha256": producer_sha,
            "output_path": "/source/proof",
        },
    )
    proof = {
        "format": "greenmind.managed-snapshot.owned-termination.v1",
        "pin_id": PIN,
        "snapshot_id": 22,
        "scope_epoch": EPOCH,
        "operation_sha256": digest,
        "input_sha256": operation["input_sha256"],
        "owner": OWNER,
        "receipt_sha256": receipt_sha,
        "marker_sha256": marker_sha,
        "startup_sha256": startup_sha,
        "source_machine_sha256": "8" * 64,
        "source_hostname": OWNER["host"],
        "producer_sha256": producer_sha,
        "consumer": consumer,
        "runner": {"pid": 222, "start_ticks": 333, "exit_code": 0},
        "observed": {
            "consumer_generation": "gone",
            "runner_generation": "gone",
            "unit_main_pid": 0,
            "unit_active_state": "inactive",
            "cgroup": "absent",
        },
    }
    proof_path, proof_sha = write("proof", proof)
    binding = {
        "scope_epoch": EPOCH,
        "receipt_sha256": receipt_sha,
        "marker_sha256": marker_sha,
        "startup_sha256": startup_sha,
        "producer_scope_sha256": producer_scope_sha,
        "proof_sha256": proof_sha,
        "producer_sha256": producer_sha,
        "source_machine_sha256": "8" * 64,
        "source_hostname": OWNER["host"],
    }
    assert (
        validate_owned_termination(
            ledger=ledger,
            pin_id=PIN,
            operation=operation,
            binding=binding,
            proof_path=proof_path,
            receipt_path=receipt_path,
            marker_path=marker_path,
            startup_path=startup_path,
            producer_path=producer_path,
            producer_scope_path=producer_scope_path,
        )
        == proof_sha
    )
    proof["consumer"]["start_ticks"] += 1
    proof_path.chmod(0o600)
    proof_path, binding["proof_sha256"] = write("proof", proof)
    with pytest.raises(ArchiveBlocked, match="consumer generation"):
        validate_owned_termination(
            ledger=ledger,
            pin_id=PIN,
            operation=operation,
            binding=binding,
            proof_path=proof_path,
            receipt_path=receipt_path,
            marker_path=marker_path,
            startup_path=startup_path,
            producer_path=producer_path,
            producer_scope_path=producer_scope_path,
        )
    if kind == "raw_pilot":

        def replay_guard(pin, bound_operation, _manifest):
            return validate_owned_termination(
                ledger=ledger,
                pin_id=pin,
                operation=bound_operation,
                binding=binding,
                proof_path=proof_path,
                receipt_path=receipt_path,
                marker_path=marker_path,
                startup_path=startup_path,
                producer_path=producer_path,
                producer_scope_path=producer_scope_path,
            )

        with lease("copy"):
            with pytest.raises(ArchiveBlocked, match="consumer generation"):
                ledger.release_completed_pilot_pin(
                    pin_id=PIN,
                    operation_path=operation_path,
                    operation_sha256=digest,
                    owner=OWNER,
                    replay_guard=replay_guard,
                )
            assert ledger.db.execute(
                "SELECT state FROM managed_snapshot_pin WHERE id=?", (PIN,)
            ).fetchone() == ("active",)
            proof["consumer"]["start_ticks"] -= 1
            proof_path.chmod(0o600)
            proof_path, binding["proof_sha256"] = write("proof", proof)
            ledger.release_completed_pilot_pin(
                pin_id=PIN,
                operation_path=operation_path,
                operation_sha256=digest,
                owner=OWNER,
                replay_guard=replay_guard,
            )
            assert ledger.db.execute(
                "SELECT state,completion_sha256 FROM managed_snapshot_pin WHERE id=?", (PIN,)
            ).fetchone() == ("released", binding["proof_sha256"])


def test_root_receipt_is_persisted_and_identical_after_lost_response(subject, monkeypatch):
    from contextlib import contextmanager
    from pathlib import Path

    import httpx

    from app.raw_archive import snapshot_api
    from app.tiered_archive import ledger as ledger_module
    from app.tiered_archive import registry_backup, runtime

    ledger, operation, digest, _ = subject
    monkeypatch.setattr(runtime, "environment", lambda: "production")
    monkeypatch.setattr(runtime.os, "geteuid", lambda: 0)
    monkeypatch.setattr(ledger_module, "_private_read", lambda path, limit: Path(path).read_bytes())
    monkeypatch.setattr(snapshot_api, "private_token", lambda *_: "secret-not-used")
    monkeypatch.setattr(snapshot_api, "SnapshotAPI", lambda *_: Provider())
    monkeypatch.setenv("TIERED_ARCHIVE_SNAPSHOT_TOKEN_FILE", "/unused")
    monkeypatch.setenv("TIERED_ARCHIVE_BOX_ID", "12")
    monkeypatch.setenv("RAW_ARCHIVE_SFTP_USER", "unused")
    monkeypatch.setenv("RAW_ARCHIVE_SFTP_HOST", "unused")

    @contextmanager
    def worker():
        with lease("copy"):
            yield ledger, None, lambda: None

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    calls = []
    monkeypatch.setattr(runtime, "worker", worker)
    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setattr(
        registry_backup,
        "backup",
        lambda *_a, **_k: calls.append(1) or {"key": "catalog", "sha256": "a" * 64, "bytes": 1},
    )
    first = runtime.managed_snapshot_pin(operation, digest)
    second = runtime.managed_snapshot_pin(operation, digest)
    assert first == second
    encoded, saved_sha = ledger.db.execute(
        "SELECT receipt,receipt_sha256 FROM managed_snapshot_pin_receipt WHERE pin_id=?", (PIN,)
    ).fetchone()
    assert encoded == json.dumps(first["pin_receipt"], sort_keys=True)
    assert saved_sha == hashlib.sha256((encoded + "\n").encode()).hexdigest()
    assert first["pin_receipt_sha256"] == saved_sha
    assert len(calls) == 3


def test_transferred_live_import_proof_requires_exact_live_owner_and_modules(subject):
    from app.tiered_archive.managed_snapshot import validate_live_import_proof

    ledger, operation_path, digest, root = subject
    operation = json.loads(operation_path.read_text())
    with lease("copy"):
        acquire(subject)

    def write(name, value, *, canonical=False):
        path = root / name
        body = (
            value
            if isinstance(value, bytes)
            else json.dumps(value, sort_keys=canonical).encode() + b"\n"
        )
        path.write_bytes(body)
        path.chmod(0o400)
        return path, hashlib.sha256(body).hexdigest()

    receipt = {
        "schema": 1,
        "state": "active",
        "pin_id": PIN,
        "snapshot_id": 22,
        "kind": "restoreinput",
        "consumer_entrypoint": "raw-restore",
        "scope_epoch": EPOCH,
        "operation_sha256": digest,
        "input_sha256": operation["input_sha256"],
        "owner": OWNER,
    }
    receipt_path, receipt_sha = write("live-receipt", receipt, canonical=True)
    with ledger.db:
        ledger.db.execute(
            "INSERT INTO managed_snapshot_pin_receipt VALUES(?,?,?)",
            (PIN, json.dumps(receipt, sort_keys=True), receipt_sha),
        )
    launcher_path, launcher_sha = write("live-launcher", b"pinned-reviewed-launcher")
    modules = [
        {
            "name": "app.raw_archive.recovery",
            "path": "/runtime/backend/app/raw_archive/recovery.py",
            "sha256": "3" * 64,
        },
        {
            "name": "app.raw_archive.snapshot_api",
            "path": "/runtime/backend/app/raw_archive/snapshot_api.py",
            "sha256": "4" * 64,
        },
        {
            "name": "app.raw_archive.storage",
            "path": "/runtime/backend/app/raw_archive/storage.py",
            "sha256": "5" * 64,
        },
    ]
    code_scope = {
        "schema": 1,
        "scope_epoch": EPOCH,
        "runtime_root": "/runtime",
        "manifest_sha256": "6" * 64,
        "runtime_sha256": "7" * 64,
        "launcher_path": "/root/launcher.py",
        "launcher_sha256": launcher_sha,
        "modules": modules,
    }
    code_scope_path, code_scope_sha = write("live-code-scope", code_scope)
    startup = {
        "format": "greenmind.managed-snapshot.preimport-audit.v1",
        "scope_epoch": EPOCH,
        "owner": OWNER,
        **{
            key: code_scope[key]
            for key in (
                "runtime_root",
                "manifest_sha256",
                "runtime_sha256",
                "launcher_path",
                "launcher_sha256",
            )
        },
    }
    startup_path, startup_sha = write("live-startup", startup)
    proof = {
        "format": "greenmind.managed-snapshot.live-imports.v1",
        "scope_epoch": EPOCH,
        "pin_id": PIN,
        "operation_sha256": digest,
        "owner": OWNER,
        "entrypoint": "raw-restore",
        "code_scope_sha256": code_scope_sha,
        "startup_proof_sha256": startup_sha,
        "modules": modules,
    }
    proof_path, proof_sha = write("live-proof", proof)
    binding = {
        "proof_sha256": proof_sha,
        "startup_sha256": startup_sha,
        "code_scope_sha256": code_scope_sha,
        "receipt_sha256": receipt_sha,
        "launcher_sha256": launcher_sha,
    }
    unit = {
        "name": OWNER["unit"],
        "active_state": "active",
        "main_pid": 123,
        "main_start_ticks": 456,
        "invocation": OWNER["invocation"],
        "cgroup": "/system.slice/" + OWNER["unit"],
        "scope_environment": {
            "RAW_ARCHIVE_MANAGED_SCOPE_REQUIRED": "true",
            "RAW_ARCHIVE_MANAGED_SCOPE_EPOCH": EPOCH,
        },
        "modules": [{**item, "uid": 0, "mode": 292, "device": 1, "inode": 2} for item in modules],
    }
    args = dict(
        ledger=ledger,
        unit=unit,
        expected_hostname=OWNER["host"],
        expected_runtime_sha256="7" * 64,
        expected_manifest_sha256="6" * 64,
        expected_launcher_sha256=launcher_sha,
        binding=binding,
        proof_path=proof_path,
        startup_path=startup_path,
        code_scope_path=code_scope_path,
        receipt_path=receipt_path,
        launcher_path=launcher_path,
    )
    assert validate_live_import_proof(**args) == PIN
    unit["main_start_ticks"] += 1
    with pytest.raises(ArchiveBlocked, match="process generation"):
        validate_live_import_proof(**args)
