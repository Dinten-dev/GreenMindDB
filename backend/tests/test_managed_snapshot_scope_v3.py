"""Local contract integration; root and Linux identity are simulated."""

import hashlib
import json
import os
import socket
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.raw_archive.coordination import lease
from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.remote_lease import bound_source_lease
from app.tiered_archive import managed_snapshot as subject
from app.tiered_archive import runtime
from app.tiered_archive.ledger import Ledger, initialize_snapshot_pins
from app.tiered_archive.registry_backup import initialize_managed


def test_new_epoch_and_zero_pin_guard_with_independent_v2_handoffs(tmp_path, monkeypatch):
    import app.raw_archive.storage  # noqa: F401

    epoch, runtime_sha = "a" * 32, "b" * 64
    source_machine, archive_machine = "c" * 64, hashlib.sha256(b"archive-machine").hexdigest()
    boot = "12345678-1234-1234-1234-123456789abc"
    coord = tmp_path / "coord"
    coord.mkdir(mode=0o700)
    monkeypatch.setenv("RAW_ARCHIVE_COORDINATION_DIR", str(coord))
    monkeypatch.setattr(runtime, "runtime_sha256", lambda: runtime_sha)
    ledger = Ledger(tmp_path / "state")
    initialize_managed(ledger.db)
    initialize_snapshot_pins(ledger.db)
    ledger.db.commit()
    raw_journal = tmp_path / "raw.sqlite3"
    with sqlite3.connect(raw_journal) as db:
        db.execute("CREATE TABLE archive(state TEXT,receipt TEXT)")
    raw_journal.chmod(0o600)
    collector = tmp_path / "collector.py"
    collector.write_bytes(
        (
            Path(subject.__file__).resolve().parents[1]
            / "raw_archive/snapshot_roster_collector_v2.py"
        ).read_bytes()
    )
    collector.chmod(0o400)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setenv("INVOCATION_ID", "f" * 32)

    # Exercise the actual validators without claiming native root/Linux evidence.
    real_stat, real_bytes, real_text = Path.stat, Path.read_bytes, Path.read_text

    class RootStat:
        def __init__(self, original):
            self.original = original
            self.st_uid = 0
            self.st_mode = (
                original.st_mode & ~0o022
                if (original.st_mode & 0o170000) == 0o040000
                else original.st_mode
            )

        def __getattr__(self, name):
            return getattr(self.original, name)

    def fake_stat(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        return (
            RootStat(result)
            if path == tmp_path
            or path in tmp_path.parents
            or path in {tmp_path / "handoff.json", tmp_path / "handoff-ready.json"}
            else result
        )

    monkeypatch.setattr(Path, "stat", fake_stat)
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda path: b"archive-machine" if str(path) == "/etc/machine-id" else real_bytes(path),
    )

    def fake_text(path, *args, **kwargs):
        if str(path) == "/proc/sys/kernel/random/boot_id":
            return boot
        if str(path) == "/proc/self/stat":
            return "123 (python) " + " ".join(["0"] * 19 + ["123"])
        if str(path) == "/proc/self/cgroup":
            return "0::/system.slice/archive.service\n"
        return real_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fake_text)

    def write(path, value):
        if path.exists():
            path.chmod(0o600)
        path.write_text(json.dumps(value, sort_keys=True))
        path.chmod(0o400)
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def file_binding(path):
        info = path.stat()
        return {
            "path": str(path),
            "device": info.st_dev,
            "inode": info.st_ino,
            "owner": info.st_uid,
            "mode": info.st_mode & 0o7777,
        }

    app_root = Path(subject.__file__).resolve().parents[1]
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
    dependency_roots = {
        role: [{"path": "/reviewed/" + role, "name_regex": ".*"}] for role in ("source", "archive")
    }
    policies = {}
    roster_parts = {}
    for role in ("source", "archive"):
        hostname = "source" if role == "source" else socket.gethostname()
        machine = source_machine if role == "source" else archive_machine
        unit = {
            "name": role + ".service",
            "unit_files": [],
            "environment_files": [],
            "dropin_files": [],
            "modules": [],
        }
        unit_sha = hashlib.sha256(subject._json(unit).encode()).hexdigest()
        review = {
            "format": "greenmind.managed-snapshot.unit-exclusion.v1",
            "scope_epoch": epoch,
            "role": role,
            "unit": unit["name"],
            "unit_plan_sha256": unit_sha,
            "reason": "sensor-receiver-no-snapshot",
        }
        review_path = tmp_path / (role + "-review.json")
        policies[role] = [
            {
                "name": unit["name"],
                "mode": "excluded",
                "plan_sha256": unit_sha,
                "review_path": str(review_path),
                "review_sha256": write(review_path, review),
            }
        ]
        if role == "archive":
            policies[role] = [
                {"name": unit["name"], "mode": "archive_worker", "plan_sha256": unit_sha}
            ]
        plan = {
            "format": "greenmind.managed-snapshot.root-roster-plan.v2",
            "role": role,
            "hostname": hostname,
            "machine_sha256": machine,
            "boot_id": boot,
            "scope_epoch": epoch,
            "collector_sha256": subject._ROSTER_V2_COLLECTOR,
            "runtime": {"runtime_sha256": runtime_sha},
            "new_snapshot_ids": [],
            "units": [unit],
            "journals": [],
            "dependency_roots": dependency_roots[role],
        }
        roster = {
            "format": "greenmind.managed-snapshot.consumer-roster.v2",
            "status": "HOLD",
            "reasons": ["legacy_latest_coverage_unproven"],
            "unknown_units": [],
            "missing_units": [],
            "plan_sha256": "",
            "scope_epoch": epoch,
            "role": role,
            "hostname": hostname,
            "machine_sha256": machine,
            "boot_id": boot,
            "observed_at": datetime.now(UTC).isoformat(),
            "collector_sha256": subject._ROSTER_V2_COLLECTOR,
            "runtime_sha256": runtime_sha,
            "source_lease_profile": {"sha256": "d" * 64},
            "units": [unit],
            "root_operations": [],
            "root_operation_journals": [],
            "legacy_latest_snapshot_consumers": [],
            "dependency_matches": [],
        }
        if role == "archive":
            roster["units"] = [
                unit
                | {
                    "main_pid": os.getpid(),
                    "main_start_ticks": 123,
                    "main_uid": 0,
                    "active_state": "active",
                    "cgroup": "/system.slice/archive.service",
                    "invocation": "f" * 32,
                    "scope_environment": {
                        "RAW_ARCHIVE_MANAGED_SCOPE_REQUIRED": "true",
                        "RAW_ARCHIVE_MANAGED_SCOPE_EPOCH": epoch,
                    },
                }
            ]
        roster_parts[role] = (plan, roster)

    def handoff(ids):
        result = {"schema": 1, "scope_epoch": epoch}
        for role, (plan, roster) in roster_parts.items():
            plan["new_snapshot_ids"] = ids
            plan_path, roster_path = (
                tmp_path / (role + "-plan.json"),
                tmp_path / (role + "-roster.json"),
            )
            plan_sha = write(plan_path, plan)
            roster["plan_sha256"] = plan_sha
            roster["observed_at"] = datetime.now(UTC).isoformat()
            result[role] = {
                "binding": {
                    "roster_path": str(roster_path),
                    "roster_sha256": write(roster_path, roster),
                    "plan_path": str(plan_path),
                    "plan_sha256": plan_sha,
                    "collector_path": str(collector),
                    "collector_sha256": subject._ROSTER_V2_COLLECTOR,
                    "hostname": plan["hostname"],
                    "machine_sha256": plan["machine_sha256"],
                    "boot_id": boot,
                },
                "evidence": [],
            }
        handoff_path = tmp_path / "handoff.json"
        monkeypatch.setenv("TIERED_ARCHIVE_SNAPSHOT_HANDOFF_FILE", str(handoff_path))
        monkeypatch.setenv("TIERED_ARCHIVE_SNAPSHOT_HANDOFF_SHA256", write(handoff_path, result))

    class Provider:
        box_id = 12

    with lease("copy"), bound_source_lease(lambda: None):
        contract = {
            "schema": 3,
            "environment": "production",
            "box_id": 12,
            "epoch": epoch,
            "runtime_sha256": runtime_sha,
            "journal": file_binding(ledger.root / "tiered.sqlite3"),
            "coordination": file_binding(coord / "jobs.lock"),
            "source_lease": {
                "host": "source",
                "machine_sha256": source_machine,
                "profile_sha256": "d" * 64,
                "owned_producer_sha256": subject._REVIEWED_OWNED_PRODUCER,
                "runtime_sha256": runtime_sha,
            },
            "archive_identity": {"host": socket.gethostname(), "machine_sha256": archive_machine},
            "raw_ledgers": [file_binding(raw_journal)],
            "pilot_manifests": [],
            "restore_descriptors": [],
            "consumers": consumers,
            "roster_policies": policies,
            "dependency_roots": dependency_roots,
        }
        config_path = tmp_path / "contract.json"
        config_sha = write(config_path, contract)
        monkeypatch.setenv("TIERED_ARCHIVE_SNAPSHOT_DEPENDENCIES_FILE", str(config_path))
        monkeypatch.setenv("TIERED_ARCHIVE_SNAPSHOT_DEPENDENCIES_SHA256", config_sha)
        handoff([])
        pointer = tmp_path / "handoff-ready.json"
        write(
            pointer,
            {
                "schema": 1,
                "scope_epoch": epoch,
                "path": str(tmp_path / "handoff.json"),
                "sha256": os.environ["TIERED_ARCHIVE_SNAPSHOT_HANDOFF_SHA256"],
            },
        )
        monkeypatch.setenv("TIERED_ARCHIVE_SNAPSHOT_HANDOFF_READY", str(pointer))
        monkeypatch.delenv("TIERED_ARCHIVE_SNAPSHOT_HANDOFF_SHA256")
        subject.reconcile_scope(
            ledger, Provider(), contract=contract, contract_sha256=config_sha, activate=True
        )
        assert ledger.db.execute(
            "SELECT recovery_state FROM managed_snapshot_scope"
        ).fetchone() == ("current",)
        victim = {
            "id": 22,
            "storage_box": 12,
            "name": "managed-22",
            "created": datetime.now(UTC).isoformat(),
        }
        with ledger.db:
            ledger.db.execute(
                "INSERT INTO managed_snapshot VALUES(?,'verified','{}',?,'{}',?)",
                ("e" * 32, json.dumps(victim), datetime.now(UTC).isoformat()),
            )
            ledger.db.execute("INSERT INTO managed_snapshot_epoch VALUES(?,?)", ("e" * 32, epoch))
            ledger.db.execute("UPDATE managed_snapshot_scope SET recovery_state='needs_reconcile'")
        handoff([])
        with pytest.raises(ArchiveBlocked, match="identity, epoch or complete inventory"):
            subject.reconcile_scope(
                ledger, Provider(), contract=contract, contract_sha256=config_sha
            )
        assert ledger.db.execute(
            "SELECT recovery_state FROM managed_snapshot_scope"
        ).fetchone() == ("needs_reconcile",)
        handoff([22])
        subject.reconcile_scope(ledger, Provider(), contract=contract, contract_sha256=config_sha)
        subject.check_external_dependencies(victim, [], ledger=ledger, provider=Provider())
        with ledger.db:
            ledger.db.execute(
                "INSERT INTO managed_snapshot_pin VALUES(?,?,?,?,?,?,'active',?,NULL,NULL)",
                (
                    "f" * 32,
                    "e" * 32,
                    22,
                    "raw_pilot",
                    "a" * 64,
                    "{}",
                    datetime.now(UTC).isoformat(),
                ),
            )
        with pytest.raises(ArchiveBlocked, match="active or uncertain pins"):
            subject.check_external_dependencies(victim, [], ledger=ledger, provider=Provider())
        with ledger.db:
            ledger.db.execute("DELETE FROM managed_snapshot_pin")
        roster_parts["source"][1]["dependency_matches"] = ["/reviewed/source/unknown"]
        handoff([22])
        with pytest.raises(ArchiveBlocked, match="inventory is incomplete or nonempty"):
            subject.check_external_dependencies(victim, [], ledger=ledger, provider=Provider())
    ledger.close()
