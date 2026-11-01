"""Journalled snapshots of the existing tiered archive catalogue and its bundles."""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.raw_archive.policy import ArchiveBlocked, checksum
from app.raw_archive.snapshot_api import managed_catalog_reference, managed_snapshot_plan

from .ledger import Ledger, _private_read, initialize_snapshot_pins, initialize_snapshot_readback
from .pipeline import candidate_json, verify_file, verify_snapshot
from .policy import POLICY_SHA256, utc
from .registry_backup import backup, initialize_managed, restore
from .runtime import candidate_from_json

_REVIEWED_ROSTER_COLLECTORS = {
    "09f1626ceb40b08da1406831416b15a12a46790db34a0871f2b2142166a42c90",
    "2f6f43ddad754e6df9ae1693b3a6620abede5ac1ec4735b0069c12783f87f7a8",
    "f752e2298346176abd6e01c30e67e3f2d9ed62de4573d65605ee6f1b72433f8b",
}
_ROSTER_V2_COLLECTOR = "f752e2298346176abd6e01c30e67e3f2d9ed62de4573d65605ee6f1b72433f8b"
_REVIEWED_PILOT_LAUNCHER = "ac6138e75bd64ca3704a13c23b2e9d4da263a7a84363eecb588f05d45b1b2dd8"
_REVIEWED_RESTORE_LAUNCHER = "f852fd478d525c37986acf16a93a9a0429c0bebf9f5ff624dcf6d7aaea90c2ef"
_REVIEWED_OWNED_PRODUCER = "e8a05fd3fe6e9c4e3db2a62cd7752cb3ef784e965583785f17ef6ce64d23a113"


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _identity(snapshot):
    return {key: snapshot[key] for key in ("id", "storage_box", "name", "created")}


def _marker(snapshot, intent):
    return (
        snapshot.get("description") == "greenmind-managed-" + intent
        and snapshot.get("labels", {}).get("purpose") == "greenmind-managed"
        and snapshot.get("labels", {}).get("intent") == intent
    )


def validate_owned_termination(
    *,
    ledger,
    pin_id,
    operation,
    binding,
    proof_path,
    receipt_path,
    marker_path,
    startup_path,
    producer_path,
    producer_scope_path,
    now=None,
):
    """Validate a root-transported owned-wait proof against an active exact pin.

    The caller must independently bind the producer execution and transport;
    JSON contents by themselves do not prove that wait(2) occurred.
    """
    from .ledger import _sha

    expected = {
        "scope_epoch",
        "receipt_sha256",
        "marker_sha256",
        "startup_sha256",
        "producer_scope_sha256",
        "proof_sha256",
        "producer_sha256",
        "source_machine_sha256",
        "source_hostname",
    }
    if (
        type(binding) is not dict
        or set(binding) != expected
        or not re.fullmatch(r"[a-f0-9]{32}", str(binding["scope_epoch"]))
        or any(not _sha(binding[name]) for name in expected - {"scope_epoch", "source_hostname"})
        or type(binding["source_hostname"]) is not str
        or not binding["source_hostname"]
    ):
        raise ArchiveBlocked("Pinned owned-wait proof binding required")

    def read(path, expected_sha):
        path = Path(path)
        raw = _private_read(path, 1024 * 1024)
        info = path.stat(follow_symlinks=False)
        if (
            stat.S_IMODE(info.st_mode) != 0o400
            or info.st_nlink != 1
            or hashlib.sha256(raw).hexdigest() != expected_sha
        ):
            raise ArchiveBlocked("Owned-wait evidence file identity differs")
        if os.geteuid() == 0:
            for parent in path.parents:
                parent_info = parent.stat(follow_symlinks=False)
                if (
                    not stat.S_ISDIR(parent_info.st_mode)
                    or parent_info.st_uid != 0
                    or parent_info.st_mode & 0o022
                ):
                    raise ArchiveBlocked("Owned-wait evidence parent is untrusted")
        return raw

    proof_raw = read(proof_path, binding["proof_sha256"])
    observed_now = now or datetime.now(UTC)
    proof_time = datetime.fromtimestamp(Path(proof_path).stat(follow_symlinks=False).st_mtime, UTC)
    if not timedelta(0) <= observed_now - proof_time <= timedelta(minutes=5):
        raise ArchiveBlocked("Owned-wait proof is not fresh")
    receipt_raw = read(receipt_path, binding["receipt_sha256"])
    marker_raw = read(marker_path, binding["marker_sha256"])
    startup_raw = read(startup_path, binding["startup_sha256"])
    read(producer_path, binding["producer_sha256"])
    producer_scope_raw = read(producer_scope_path, binding["producer_scope_sha256"])
    try:
        proof, receipt, startup, marker, producer_scope = map(
            json.loads, (proof_raw, receipt_raw, startup_raw, marker_raw, producer_scope_raw)
        )
    except (TypeError, ValueError):
        raise ArchiveBlocked("Owned-wait evidence encoding differs") from None
    proof_fields = {
        "format",
        "pin_id",
        "snapshot_id",
        "scope_epoch",
        "operation_sha256",
        "input_sha256",
        "owner",
        "receipt_sha256",
        "marker_sha256",
        "startup_sha256",
        "source_machine_sha256",
        "source_hostname",
        "producer_sha256",
        "consumer",
        "runner",
        "observed",
    }
    if (
        type(proof) is not dict
        or set(proof) != proof_fields
        or proof.get("format") != "greenmind.managed-snapshot.owned-termination.v1"
    ):
        raise ArchiveBlocked("Owned-wait proof format differs")
    scope = ledger.db.execute(
        "SELECT epoch,recovery_state FROM managed_snapshot_scope WHERE id=1"
    ).fetchone()
    pin = ledger.db.execute(
        "SELECT snapshot_id,operation_sha256,owner,state,kind FROM managed_snapshot_pin WHERE id=?",
        (pin_id,),
    ).fetchone()
    if not scope or scope[1] != "current" or not pin or pin[3] not in {"active", "uncertain"}:
        raise ArchiveBlocked("Current active managed pin required")
    saved_receipt = ledger.db.execute(
        "SELECT receipt,receipt_sha256 FROM managed_snapshot_pin_receipt WHERE pin_id=?", (pin_id,)
    ).fetchone()
    if (
        not saved_receipt
        or saved_receipt[1] != binding["receipt_sha256"]
        or (saved_receipt[0] + "\n").encode() != receipt_raw
    ):
        raise ArchiveBlocked("Owned-wait receipt is not the durable issued receipt")
    owner = json.loads(pin[2])
    if (
        type(operation) is not dict
        or operation.get("pin_id") != pin_id
        or operation.get("owner") != owner
        or operation.get("scope_epoch") != scope[0]
        or operation.get("snapshot_id") != pin[0]
        or proof["pin_id"] != pin_id
        or proof["snapshot_id"] != pin[0]
        or proof["scope_epoch"] != scope[0]
        or binding["scope_epoch"] != scope[0]
        or proof["owner"] != owner
        or proof["operation_sha256"] != pin[1]
        or proof["input_sha256"] != operation.get("input_sha256")
        or proof["source_hostname"] != owner.get("host")
    ):
        raise ArchiveBlocked("Owned-wait proof pin, operation or owner differs")
    for name in (
        "receipt_sha256",
        "marker_sha256",
        "startup_sha256",
        "source_machine_sha256",
        "source_hostname",
        "producer_sha256",
    ):
        if proof[name] != binding[name]:
            raise ArchiveBlocked("Owned-wait proof binding differs")
    if (
        type(receipt) is not dict
        or receipt.get("schema") != 1
        or receipt.get("state") != "active"
        or receipt.get("pin_id") != pin_id
        or receipt.get("snapshot_id") != pin[0]
        or receipt.get("scope_epoch") != scope[0]
        or receipt.get("kind") != pin[4]
        or receipt.get("consumer_entrypoint") != operation.get("consumer_entrypoint")
        or receipt.get("operation_sha256") != pin[1]
        or receipt.get("input_sha256") != operation.get("input_sha256")
        or receipt.get("owner") != owner
    ):
        raise ArchiveBlocked("Owned-wait receipt differs")
    if marker != {
        "format": "greenmind.managed-snapshot.marker.v1",
        "pin_id": pin_id,
        "snapshot_id": pin[0],
        "scope_epoch": scope[0],
        "operation_sha256": pin[1],
        "input_sha256": operation.get("input_sha256"),
        "owner": owner,
    }:
        raise ArchiveBlocked("Owned-wait operation marker differs")
    producer_scope_fields = {
        "format",
        "receipt_path",
        "receipt_sha256",
        "marker_path",
        "marker_sha256",
        "operation_path",
        "operation_sha256",
        "startup_path",
        "startup_sha256",
        "source_machine_sha256",
        "source_hostname",
        "producer_sha256",
        "output_path",
    }
    if (
        type(producer_scope) is not dict
        or set(producer_scope) != producer_scope_fields
        or producer_scope.get("format") != "greenmind.managed-snapshot.owned-proof-scope.v1"
        or producer_scope.get("operation_sha256") != pin[1]
        or any(
            producer_scope.get(name) != binding[name]
            for name in (
                "receipt_sha256",
                "marker_sha256",
                "startup_sha256",
                "source_machine_sha256",
                "source_hostname",
                "producer_sha256",
            )
        )
        or any(
            type(producer_scope.get(name)) is not str
            or not Path(producer_scope[name]).is_absolute()
            for name in (
                "receipt_path",
                "marker_path",
                "operation_path",
                "startup_path",
                "output_path",
            )
        )
    ):
        raise ArchiveBlocked("Owned-wait producer scope differs")
    consumer = proof["consumer"]
    startup_keys = {
        "format",
        "pid",
        "start_ticks",
        "unit",
        "invocation",
        "cgroup",
        "cgroup_device",
        "cgroup_inode",
    }
    if (
        type(startup) is not dict
        or set(startup) != startup_keys
        or startup["format"] != "greenmind.managed-snapshot.consumer-startup.v1"
        or type(consumer) is not dict
        or consumer != {key: startup[key] for key in startup_keys - {"format"}}
        or type(consumer["pid"]) is not int
        or consumer["pid"] <= 0
        or type(consumer["start_ticks"]) is not int
        or consumer["start_ticks"] <= 0
        or owner.get("pid_generation") != f"{consumer['pid']}:{consumer['start_ticks']}"
        or owner.get("unit") != consumer["unit"]
        or owner.get("invocation") != consumer["invocation"]
        or type(consumer["cgroup"]) is not str
        or consumer["cgroup"] != "/system.slice/" + consumer["unit"]
        or not re.fullmatch(r"[a-zA-Z0-9_.@:-]+\.service", consumer["unit"])
        or not re.fullmatch(r"[a-f0-9]{32}", consumer["invocation"])
        or type(consumer["cgroup_device"]) is not int
        or consumer["cgroup_device"] <= 0
        or type(consumer["cgroup_inode"]) is not int
        or consumer["cgroup_inode"] <= 0
    ):
        raise ArchiveBlocked("Owned-wait consumer generation differs")
    runner = proof["runner"]
    if (
        type(runner) is not dict
        or set(runner) != {"pid", "start_ticks", "exit_code"}
        or type(runner["pid"]) is not int
        or runner["pid"] <= 0
        or runner["pid"] == consumer["pid"]
        or type(runner["start_ticks"]) is not int
        or runner["start_ticks"] <= 0
        or type(runner["exit_code"]) is not int
        or runner["exit_code"] != 0
    ):
        raise ArchiveBlocked("Owned-wait runner did not complete successfully")
    if proof["observed"] != {
        "consumer_generation": "gone",
        "runner_generation": "gone",
        "unit_main_pid": 0,
        "unit_active_state": "inactive",
        "cgroup": "absent",
    }:
        raise ArchiveBlocked("Owned-wait generation closure is unproven")
    return binding["proof_sha256"]


def validate_live_import_proof(
    *,
    ledger,
    unit,
    expected_hostname,
    expected_runtime_sha256,
    expected_manifest_sha256,
    expected_launcher_sha256,
    binding,
    proof_path,
    startup_path,
    code_scope_path,
    receipt_path,
    launcher_path,
    expected_entrypoints=None,
):
    """Validate transferred live evidence; caller must authenticate the root transport."""
    keys = {
        "proof_sha256",
        "startup_sha256",
        "code_scope_sha256",
        "receipt_sha256",
        "launcher_sha256",
    }
    if (
        type(binding) is not dict
        or set(binding) != keys
        or any(
            type(value) is not str or not re.fullmatch(r"[a-f0-9]{64}", value)
            for value in binding.values()
        )
    ):
        raise ArchiveBlocked("Pinned live import proof binding required")

    def read(path, expected):
        path = Path(path)
        raw = _private_read(path, 1024 * 1024)
        info = path.stat(follow_symlinks=False)
        if (
            stat.S_IMODE(info.st_mode) != 0o400
            or info.st_nlink != 1
            or hashlib.sha256(raw).hexdigest() != expected
        ):
            raise ArchiveBlocked("Live import evidence file differs")
        if os.geteuid() == 0:
            for parent in path.parents:
                info = parent.stat(follow_symlinks=False)
                if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                    raise ArchiveBlocked("Live import evidence parent is untrusted")
        return raw

    proof_raw = read(proof_path, binding["proof_sha256"])
    startup_raw = read(startup_path, binding["startup_sha256"])
    scope_raw = read(code_scope_path, binding["code_scope_sha256"])
    receipt_raw = read(receipt_path, binding["receipt_sha256"])
    read(launcher_path, binding["launcher_sha256"])
    try:
        proof, startup, code_scope, receipt = map(
            json.loads, (proof_raw, startup_raw, scope_raw, receipt_raw)
        )
    except (TypeError, ValueError):
        raise ArchiveBlocked("Live import evidence encoding differs") from None
    fields = {
        "format",
        "scope_epoch",
        "pin_id",
        "operation_sha256",
        "owner",
        "entrypoint",
        "code_scope_sha256",
        "startup_proof_sha256",
        "modules",
    }
    if (
        type(proof) is not dict
        or set(proof) != fields
        or proof.get("format") != "greenmind.managed-snapshot.live-imports.v1"
    ):
        raise ArchiveBlocked("Live import proof format differs")
    if expected_entrypoints is not None and proof.get("entrypoint") not in expected_entrypoints:
        raise ArchiveBlocked("Live snapshot entrypoint does not match its reviewed launcher")
    scope = ledger.db.execute(
        "SELECT epoch,recovery_state FROM managed_snapshot_scope WHERE id=1"
    ).fetchone()
    if (
        not scope
        or scope[1] not in {"current", "needs_reconcile"}
        or proof["scope_epoch"] != scope[0]
    ):
        raise ArchiveBlocked("Live import proof scope differs")
    row = ledger.db.execute(
        "SELECT snapshot_id,kind,operation_sha256,owner,state FROM managed_snapshot_pin WHERE id=?",
        (proof["pin_id"],),
    ).fetchone()
    saved = ledger.db.execute(
        "SELECT receipt,receipt_sha256 FROM managed_snapshot_pin_receipt WHERE pin_id=?",
        (proof["pin_id"],),
    ).fetchone()
    if (
        not row
        or row[4] not in {"active", "uncertain"}
        or not saved
        or saved[1] != binding["receipt_sha256"]
        or (saved[0] + "\n").encode() != receipt_raw
    ):
        raise ArchiveBlocked("Live import proof lacks its durable active pin receipt")
    owner = json.loads(row[3])
    if (
        type(unit) is not dict
        or unit.get("active_state") != "active"
        or type(unit.get("main_pid")) is not int
        or type(unit.get("main_start_ticks")) is not int
        or owner.get("host") != expected_hostname
        or owner.get("unit") != unit.get("name")
        or owner.get("invocation") != unit.get("invocation")
        or owner.get("pid_generation") != f"{unit['main_pid']}:{unit['main_start_ticks']}"
        or unit.get("cgroup") != "/system.slice/" + owner.get("unit", "")
        or unit.get("scope_environment")
        != {
            "RAW_ARCHIVE_MANAGED_SCOPE_REQUIRED": "true",
            "RAW_ARCHIVE_MANAGED_SCOPE_EPOCH": scope[0],
        }
        or proof["owner"] != owner
        or proof["operation_sha256"] != row[2]
        or type(receipt) is not dict
        or receipt.get("schema") != 1
        or receipt.get("state") != "active"
        or receipt.get("pin_id") != proof["pin_id"]
        or receipt.get("snapshot_id") != row[0]
        or receipt.get("kind") != row[1]
        or receipt.get("operation_sha256") != row[2]
        or receipt.get("scope_epoch") != scope[0]
        or receipt.get("owner") != owner
        or receipt.get("consumer_entrypoint") != proof["entrypoint"]
    ):
        raise ArchiveBlocked("Live import process generation or receipt differs")
    scope_fields = {
        "schema",
        "scope_epoch",
        "runtime_root",
        "manifest_sha256",
        "runtime_sha256",
        "launcher_path",
        "launcher_sha256",
        "modules",
    }
    startup_fields = {
        "format",
        "scope_epoch",
        "owner",
        "runtime_root",
        "manifest_sha256",
        "runtime_sha256",
        "launcher_path",
        "launcher_sha256",
    }
    if (
        type(code_scope) is not dict
        or set(code_scope) != scope_fields
        or code_scope["schema"] != 1
        or type(startup) is not dict
        or set(startup) != startup_fields
        or startup["format"] != "greenmind.managed-snapshot.preimport-audit.v1"
        or startup["owner"] != owner
        or code_scope["scope_epoch"] != scope[0]
        or any(startup[key] != code_scope[key] for key in scope_fields - {"schema", "modules"})
        or code_scope["launcher_sha256"] != binding["launcher_sha256"]
        or code_scope["launcher_sha256"] != expected_launcher_sha256
        or code_scope["runtime_sha256"] != expected_runtime_sha256
        or code_scope["manifest_sha256"] != expected_manifest_sha256
        or proof["code_scope_sha256"] != binding["code_scope_sha256"]
        or proof["startup_proof_sha256"] != binding["startup_sha256"]
    ):
        raise ArchiveBlocked("Live import startup or code scope differs")
    if (
        type(proof["modules"]) is not list
        or type(code_scope["modules"]) is not list
        or not proof["modules"]
        or any(type(item) is not dict for item in code_scope["modules"])
        or proof["modules"] != sorted(code_scope["modules"], key=lambda item: item.get("name", ""))
    ):
        raise ArchiveBlocked("Live loaded module roster differs")
    configured = unit.get("modules")
    if type(configured) is not list or any(type(item) is not dict for item in configured):
        raise ArchiveBlocked("Configured unit module roster is unknown")
    configured_triples = {
        (item.get("name"), item.get("path"), item.get("sha256")) for item in configured
    }
    if any(
        (item.get("name"), item.get("path"), item.get("sha256")) not in configured_triples
        for item in proof["modules"]
        if type(item) is dict
    ):
        raise ArchiveBlocked("Live modules differ from configured unit roster")
    if any(
        type(item) is not dict
        or set(item) != {"name", "path", "sha256"}
        or type(item["name"]) is not str
        or type(item["path"]) is not str
        or not re.fullmatch(r"[a-f0-9]{64}", str(item["sha256"]))
        for item in proof["modules"]
    ):
        raise ArchiveBlocked("Live module evidence is malformed")
    entry_modules = {
        "raw-pilot": "app.raw_archive.pilot",
        "raw-restore": "app.raw_archive.recovery",
        "wav-catalog": "app.raw_archive.wav_catalog",
        "snapshot-readback": "app.raw_archive.snapshot_api",
    }
    if proof["entrypoint"] not in entry_modules:
        raise ArchiveBlocked("Unknown live snapshot entrypoint")
    expected_names = {
        "app.raw_archive.snapshot_api",
        "app.raw_archive.storage",
        entry_modules[proof["entrypoint"]],
    }
    names = [item["name"] for item in proof["modules"]]
    if len(names) != len(set(names)) or set(names) != expected_names:
        raise ArchiveBlocked("Live module roster is incomplete")
    return proof["pin_id"]


def validate_roster_frame(binding, *, role, epoch, current_ids, scope_source, now=None):
    """Validate a root-transported collector frame; unresolved findings still hold."""
    fields = {"roster_path", "roster_sha256", "plan_path", "plan_sha256", "collector_path",
              "collector_sha256", "hostname", "machine_sha256", "boot_id"}
    if (type(binding) is not dict or set(binding) != fields or role not in {"source", "archive"}
        or binding.get("collector_sha256") not in _REVIEWED_ROSTER_COLLECTORS
        or any(type(binding.get(key)) is not str or not re.fullmatch(r"[a-f0-9]{64}", binding[key])
               for key in ("roster_sha256", "plan_sha256", "collector_sha256", "machine_sha256"))
        or type(binding.get("hostname")) is not str or not binding["hostname"]
        or type(binding.get("boot_id")) is not str or not re.fullmatch(r"[a-f0-9-]{36}", binding["boot_id"])):
        raise ArchiveBlocked("Reviewed root roster collector binding required")

    def read(path, expected, maximum):
        path = Path(path)
        raw = _private_read(path, maximum)
        info = path.stat(follow_symlinks=False)
        if stat.S_IMODE(info.st_mode) != 0o400 or info.st_nlink != 1 or hashlib.sha256(raw).hexdigest() != expected:
            raise ArchiveBlocked("Root roster evidence file differs")
        if os.geteuid() == 0:
            for parent in path.parents:
                metadata = parent.stat(follow_symlinks=False)
                if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_mode & 0o022:
                    raise ArchiveBlocked("Root roster evidence parent is untrusted")
        return raw

    roster_raw = read(binding["roster_path"], binding["roster_sha256"], 8 * 1024 * 1024)
    plan_raw = read(binding["plan_path"], binding["plan_sha256"], 1024 * 1024)
    read(binding["collector_path"], binding["collector_sha256"], 1024 * 1024)
    try:
        roster, plan = json.loads(roster_raw), json.loads(plan_raw)
    except (TypeError, ValueError):
        raise ArchiveBlocked("Root roster evidence encoding differs") from None
    roster_fields = {"format", "status", "reasons", "unknown_units", "missing_units", "plan_sha256",
                     "scope_epoch", "role", "hostname", "machine_sha256", "boot_id", "observed_at",
                     "collector_sha256", "runtime_sha256", "source_lease_profile", "units", "root_operations",
                     "root_operation_journals", "legacy_latest_snapshot_consumers"}
    if type(roster) is dict and roster.get("format") == "greenmind.managed-snapshot.consumer-roster.v2":
        roster_fields.add("dependency_matches")
    if (type(roster) is not dict or set(roster) != roster_fields or type(plan) is not dict
        or roster.get("format") not in {"greenmind.managed-snapshot.consumer-roster.v1", "greenmind.managed-snapshot.consumer-roster.v2"}
        or plan.get("format") not in {"greenmind.managed-snapshot.root-roster-plan.v1", "greenmind.managed-snapshot.root-roster-plan.v2"}
        or roster["format"].rsplit(".", 1)[-1] != plan["format"].rsplit(".", 1)[-1]
        or roster["scope_epoch"] != epoch or plan.get("scope_epoch") != epoch
        or roster["role"] != role or plan.get("role") != role
        or roster["plan_sha256"] != binding["plan_sha256"]
        or roster["collector_sha256"] != binding["collector_sha256"]
        or plan.get("collector_sha256") != binding["collector_sha256"]
        or type(plan.get("runtime")) is not dict
        or not re.fullmatch(r"[a-f0-9]{64}", str(plan["runtime"].get("runtime_sha256", "")))
        or roster["runtime_sha256"] != plan["runtime"]["runtime_sha256"]
        or any(roster.get(key) != binding[key] or plan.get(key) != binding[key] for key in ("hostname", "machine_sha256", "boot_id"))
        or type(plan.get("new_snapshot_ids")) is not list or sorted(plan["new_snapshot_ids"]) != sorted(current_ids)
        or type(roster["unknown_units"]) is not list or roster["unknown_units"]
        or type(roster["missing_units"]) is not list or roster["missing_units"]
        or type(roster["reasons"]) is not list
        or any(type(reason) is not str for reason in roster["reasons"])
        or len(set(roster["reasons"])) != len(roster["reasons"])
        or roster["status"] != ("HOLD" if roster["reasons"] else "PASS")):
        raise ArchiveBlocked("Root roster identity, epoch or complete inventory differs")
    try:
        observed = datetime.fromisoformat(roster["observed_at"].replace("Z", "+00:00"))
        moment = now or datetime.now(UTC)
        if observed.utcoffset() is None or not timedelta(0) <= moment - observed <= timedelta(minutes=5):
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        raise ArchiveBlocked("Root roster evidence is not fresh") from None
    if (type(scope_source) is not dict or role == "source" and (
        binding["hostname"] != scope_source.get("host") or binding["machine_sha256"] != scope_source.get("machine_sha256")
        or type(roster["source_lease_profile"]) is not dict
        or roster["source_lease_profile"].get("sha256") != scope_source.get("profile_sha256"))):
        raise ArchiveBlocked("Root roster source authority differs")
    if (type(roster["units"]) is not list or type(plan.get("units")) is not list
        or len(roster["units"]) != len(plan["units"]) or len(roster["units"]) > 512):
        raise ArchiveBlocked("Root roster unit coverage differs")
    units = {unit.get("name"): unit for unit in roster["units"] if type(unit) is dict and type(unit.get("name")) is str}
    plans = {unit.get("name"): unit for unit in plan["units"] if type(unit) is dict and type(unit.get("name")) is str}
    if len(units) != len(roster["units"]) or len(plans) != len(plan["units"]) or set(units) != set(plans):
        raise ArchiveBlocked("Root roster unit identity differs")
    for name, unit in units.items():
        for key in ("unit_files", "environment_files", "dropin_files", "modules"):
            if unit.get(key) != plans[name].get(key):
                raise ArchiveBlocked("Root roster unit file bindings differ")
    if (type(roster["root_operation_journals"]) is not list or type(plan.get("journals")) is not list
        or type(roster["root_operations"]) is not list or type(roster["legacy_latest_snapshot_consumers"]) is not list):
        raise ArchiveBlocked("Root roster operation journals differ")
    expected_journals = {(row["path"], row["device"], row["inode"], row["owner"], row["mode"], row["schema"])
                         for row in plan["journals"] if type(row) is dict and set(row) == {"path", "device", "inode", "owner", "mode", "schema"}}
    actual_journals = {(row["path"], row["device"], row["inode"], row["uid"], row["mode"], row["schema"])
                       for row in roster["root_operation_journals"] if type(row) is dict and set(row) == {"path", "sha256", "uid", "mode", "device", "inode", "schema", "operations"}}
    if len(expected_journals) != len(plan["journals"]) or len(actual_journals) != len(roster["root_operation_journals"]) or expected_journals != actual_journals:
        raise ArchiveBlocked("Root operation journal identities differ")
    return roster, plan


def reconcile_root_operations(ledger, *, epoch, current_ids, rosters, terminating_pin=None):
    """Compare fresh root journals with durable pins while holding jobs.lock.

    Callers must independently authenticate and validate both roster frames first.
    This check does not change recovery_state or release any pin.
    """
    from app.raw_archive.coordination import bound_job_lock

    bound_job_lock()
    scope = ledger.db.execute(
        "SELECT epoch,recovery_state FROM managed_snapshot_scope WHERE id=1"
    ).fetchone()
    if not scope or scope[0] != epoch or scope[1] not in {"current", "needs_reconcile"}:
        raise ArchiveBlocked("Root operation scope differs")
    if type(current_ids) is not list or any(type(i) is not int or i <= 0 for i in current_ids):
        raise ArchiveBlocked("Root operation snapshot inventory differs")
    if len(set(current_ids)) != len(current_ids) or type(rosters) is not list or len(rosters) != 2:
        raise ArchiveBlocked("Root operation inventory is incomplete")
    pins = {}
    for pin_id, snapshot_id, digest, owner_raw, state, pin_epoch in ledger.db.execute(
        "SELECT p.id,p.snapshot_id,p.operation_sha256,p.owner,p.state,e.epoch "
        "FROM managed_snapshot_pin p LEFT JOIN managed_snapshot_epoch e ON e.intent=p.intent"
    ):
        if state not in {"active", "uncertain", "released"}:
            raise ArchiveBlocked("Unknown snapshot pin state")
        if snapshot_id not in current_ids:
            if state in {"active", "uncertain"} and (pin_epoch is None or pin_epoch == epoch):
                raise ArchiveBlocked("Current scope pin is missing from snapshot inventory")
            continue
        if pin_epoch != epoch:
            raise ArchiveBlocked("Snapshot pin epoch differs")
        try:
            owner = json.loads(owner_raw)
        except (TypeError, ValueError):
            raise ArchiveBlocked("Snapshot pin owner invalid") from None
        saved = ledger.db.execute(
            "SELECT receipt,receipt_sha256 FROM managed_snapshot_pin_receipt WHERE pin_id=?",
            (pin_id,),
        ).fetchone()
        if state in {"active", "uncertain"}:
            if not saved or hashlib.sha256((saved[0] + "\n").encode()).hexdigest() != saved[1]:
                raise ArchiveBlocked("Active snapshot pin receipt unavailable")
            try:
                receipt = json.loads(saved[0])
            except (TypeError, ValueError):
                raise ArchiveBlocked("Active snapshot pin receipt invalid") from None
            if (type(receipt) is not dict or receipt.get("state") != "active"
                or receipt.get("scope_epoch") != epoch or receipt.get("pin_id") != pin_id
                or receipt.get("snapshot_id") != snapshot_id
                or receipt.get("operation_sha256") != digest or receipt.get("owner") != owner):
                raise ArchiveBlocked("Active snapshot pin receipt differs")
        pins[pin_id] = (snapshot_id, digest, owner, state)
    found = {}
    for roster in rosters:
        if type(roster) is not dict or type(roster.get("root_operation_journals")) is not list:
            raise ArchiveBlocked("Root operation journal inventory unavailable")
        derived = []
        for journal in roster["root_operation_journals"]:
            if type(journal) is not dict or type(journal.get("operations")) is not list:
                raise ArchiveBlocked("Root operation journal invalid")
            for row in journal["operations"]:
                if (type(row) is not dict or set(row) != {"pin_id", "snapshot_id", "scope_epoch", "operation_sha256", "owner", "state"}
                    or row["state"] not in {"active", "uncertain", "complete", "expired"}):
                    raise ArchiveBlocked("Root operation row invalid")
                if (type(row["snapshot_id"]) is not int or row["snapshot_id"] <= 0
                    or type(row["scope_epoch"]) is not str
                    or not re.fullmatch(r"[a-f0-9]{32}", row["scope_epoch"])):
                    raise ArchiveBlocked("Root operation identity invalid")
                if row["snapshot_id"] not in current_ids:
                    if row["scope_epoch"] == epoch:
                        historical = ledger.db.execute(
                            "SELECT p.snapshot_id,p.operation_sha256,p.owner,p.state,s.state,e.epoch "
                            "FROM managed_snapshot_pin p JOIN managed_snapshot s ON s.intent=p.intent "
                            "JOIN managed_snapshot_epoch e ON e.intent=p.intent WHERE p.id=?",
                            (row["pin_id"],),
                        ).fetchone()
                        expected = (
                            row["snapshot_id"], row["operation_sha256"],
                            _json(row["owner"]), "released", "deleted", epoch,
                        )
                        if row["state"] not in {"complete", "expired"} or historical != expected:
                            raise ArchiveBlocked("Current scope root operation is missing from snapshot inventory")
                    continue
                pin = pins.get(row["pin_id"])
                if (row["scope_epoch"] != epoch or not pin
                    or (row["snapshot_id"], row["operation_sha256"], row["owner"]) != pin[:3]
                    or row["pin_id"] in found):
                    raise ArchiveBlocked("Root operation does not match a unique issued pin")
                found[row["pin_id"]] = row["state"]
                if row["state"] in {"active", "uncertain"}:
                    derived.append({k: row[k] for k in ("pin_id", "snapshot_id", "operation_sha256", "owner", "state")} | {
                        "journal_path": journal["path"], "journal_device": journal["device"], "journal_inode": journal["inode"]
                    })
        if roster.get("root_operations") != derived:
            raise ArchiveBlocked("Root operation projection differs from journal")
    for pin_id, (_, _, _, state) in pins.items():
        observed = found.get(pin_id)
        if (state in {"active", "uncertain"} and observed != state
            and not (pin_id == terminating_pin and observed == "complete")):
            raise ArchiveBlocked("Active or uncertain snapshot pin lacks matching root operation")
        if state == "released" and observed in {"active", "uncertain"}:
            raise ArchiveBlocked("Released pin still has an active root operation")
    return None


def validate_roster_attestations(ledger, *, roster, plan, policies, evidence):
    """Check pinned unit reviews or process-bound import proofs after frame validation.

    `policies` must come from the immutable epoch contract; `evidence` is fresh
    and must arrive through the authenticated root transport. Neither a PASS
    field nor a configured-only module list proves loaded code.
    """
    if (type(policies) is not list or type(evidence) is not list
        or type(roster.get("units")) is not list or type(plan.get("units")) is not list):
        raise ArchiveBlocked("Snapshot unit attestation inventory required")
    units = {item["name"]: item for item in roster["units"]}
    plans = {item["name"]: item for item in plan["units"]}
    rules = {item.get("name"): item for item in policies if type(item) is dict}
    proofs = {item.get("name"): item for item in evidence if type(item) is dict}
    if (len(rules) != len(policies) or len(proofs) != len(evidence)
        or set(units) != set(plans) or set(units) != set(rules)):
        raise ArchiveBlocked("Snapshot unit policy coverage differs")
    allowed_reasons = {
        "legacy_latest_coverage_unproven", "unfenced_relevant_unit",
        "loaded_module_binding_unproven", "legacy_latest_fence_unproven",
        "active_or_uncertain_new_snapshot_operations",
    }
    if (type(roster.get("reasons")) is not list
        or any(reason not in allowed_reasons for reason in roster["reasons"])):
        raise ArchiveBlocked("Unresolved root roster findings")
    live_names = set()
    for name, rule in rules.items():
        plan_hash = hashlib.sha256(_json(plans[name]).encode()).hexdigest()
        if rule.get("plan_sha256") != plan_hash:
            raise ArchiveBlocked("Reviewed unit execution or profile differs")
        if rule.get("mode") == "excluded":
            if set(rule) != {"name", "mode", "plan_sha256", "review_path", "review_sha256"} or name in proofs:
                raise ArchiveBlocked("Reviewed unit exclusion binding differs")
            review_path = Path(rule["review_path"])
            raw = _private_read(review_path, 1024 * 1024)
            info = review_path.stat(follow_symlinks=False)
            if (stat.S_IMODE(info.st_mode) != 0o400 or info.st_nlink != 1
                or hashlib.sha256(raw).hexdigest() != rule["review_sha256"]):
                raise ArchiveBlocked("Reviewed unit exclusion file differs")
            try:
                review = json.loads(raw)
            except (TypeError, ValueError):
                raise ArchiveBlocked("Reviewed unit exclusion invalid") from None
            if (type(review) is not dict or set(review) != {"format", "scope_epoch", "role", "unit", "unit_plan_sha256", "reason"}
                or review["format"] != "greenmind.managed-snapshot.unit-exclusion.v1"
                or review["scope_epoch"] != roster["scope_epoch"] or review["role"] != roster["role"]
                or review["unit"] != name or review["unit_plan_sha256"] != plan_hash
                or review["reason"] not in {"feature-live-export-no-snapshot", "sensor-receiver-no-snapshot", "metadata-probe-no-snapshot", "source-api-no-snapshot"}):
                raise ArchiveBlocked("Reviewed unit exclusion scope differs")
        elif rule.get("mode") == "live":
            if (set(rule) != {"name", "mode", "plan_sha256", "launcher_sha256"}
                or roster["role"] != "source"
                or rule["launcher_sha256"] not in {_REVIEWED_PILOT_LAUNCHER, _REVIEWED_RESTORE_LAUNCHER}):
                raise ArchiveBlocked("Live snapshot unit binding differs")
            if name not in proofs:
                unit, unit_plan = units[name], plans[name]
                if (unit.get("active_state") != "inactive" or unit.get("main_pid") != 0
                    or unit.get("main_start_ticks") != 0 or unit.get("invocation")
                    or not any(module.get("path") == unit_plan.get("entrypoint")
                               and module.get("sha256") == rule["launcher_sha256"]
                               for module in unit_plan.get("modules", []) if type(module) is dict)):
                    raise ArchiveBlocked("Inactive snapshot unit lacks reviewed launcher binding")
                continue
            item = proofs[name]
            fields = {"name", "binding", "proof_path", "startup_path", "code_scope_path", "receipt_path", "launcher_path"}
            if set(item) != fields:
                raise ArchiveBlocked("Live snapshot unit evidence differs")
            validate_live_import_proof(
                ledger=ledger, unit=units[name], expected_hostname=roster["hostname"],
                expected_runtime_sha256=plan["runtime"]["runtime_sha256"],
                expected_manifest_sha256=plan["runtime"]["manifest_sha256"],
                expected_launcher_sha256=rule["launcher_sha256"],
                expected_entrypoints=({"raw-pilot"} if rule["launcher_sha256"] == _REVIEWED_PILOT_LAUNCHER
                                      else {"raw-restore", "wav-catalog"}),
                **{key: item[key] for key in fields - {"name"}},
            )
            live_names.add(name)
        elif rule.get("mode") == "archive_worker":
            import sys

            from app.raw_archive.snapshot_api import check_loaded_function_code

            unit = units[name]
            try:
                fields = Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()
                ticks = int(fields[19])
                groups = Path("/proc/self/cgroup").read_text().splitlines()
                cgroups = [row[3:] for row in groups if row.startswith("0::")]
            except (OSError, IndexError, ValueError):
                raise ArchiveBlocked("Archive worker process generation unavailable") from None
            if (set(rule) != {"name", "mode", "plan_sha256"} or roster["role"] != "archive"
                or name in proofs or os.geteuid() != 0 or unit.get("main_pid") != os.getpid()
                or unit.get("main_start_ticks") != ticks or unit.get("main_uid") != 0
                or unit.get("active_state") != "active" or cgroups != [unit.get("cgroup")]
                or unit.get("invocation") != os.getenv("INVOCATION_ID")
                or not re.fullmatch(r"[a-f0-9]{32}", unit.get("invocation") or "")
                or unit.get("scope_environment") != {
                    "RAW_ARCHIVE_MANAGED_SCOPE_REQUIRED": "true",
                    "RAW_ARCHIVE_MANAGED_SCOPE_EPOCH": roster["scope_epoch"],
                }):
                raise ArchiveBlocked("Archive worker is not the inventoried live generation")
            for module_name, functions in {
                "app.tiered_archive.managed_snapshot": ("cycle", "check_external_dependencies"),
                "app.raw_archive.storage": ("StorageBox.download_snapshot", "StorageBox.download_recovery", "StorageBox.download_catalog"),
                "app.tiered_archive.storage": ("Store.download",),
            }.items():
                module = sys.modules.get(module_name)
                if module is None:
                    raise ArchiveBlocked("Archive snapshot consumer module is not loaded")
                path = Path(module.__file__).resolve(strict=True)
                expected_path = Path(__file__).resolve().parents[1] / (module_name.removeprefix("app.").replace(".", "/") + ".py")
                if path != expected_path:
                    raise ArchiveBlocked("Archive snapshot consumer module path differs")
                check_loaded_function_code(module, path, path.read_bytes(), functions)
        else:
            raise ArchiveBlocked("Unknown snapshot unit classification")
    if set(proofs) != live_names:
        raise ArchiveBlocked("Unexpected live snapshot unit evidence")
    return None


def _root_handoff_pointer(epoch):
    """Wait briefly for Main's atomic root handoff while the current worker is inventoried."""
    import time

    from app.raw_archive.coordination import bound_job_lock
    from app.raw_archive.remote_lease import require_bound_source_lease, source_checkpoint

    pointer = Path(os.environ.get("TIERED_ARCHIVE_SNAPSHOT_HANDOFF_READY", ""))
    if not pointer.is_absolute() or pointer.is_symlink() or pointer.resolve(strict=False) != pointer:
        raise ArchiveBlocked("Absolute root handoff pointer required")
    for parent in pointer.parents:
        info = parent.stat(follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ArchiveBlocked("Root handoff pointer parent is untrusted")
    waiting_value = os.environ.get("TIERED_ARCHIVE_SNAPSHOT_HANDOFF_WAITING")
    if waiting_value and not pointer.exists():
        waiting = Path(waiting_value)
        if (not waiting.is_absolute() or waiting.parent != pointer.parent or waiting == pointer
            or waiting.resolve(strict=False) != waiting or waiting.is_symlink()):
            raise ArchiveBlocked("Fresh root waiting marker path required")
        require_bound_source_lease()
        lock_path, lock = bound_job_lock()
        try:
            ticks = int(Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()[19])
            invocation = os.environ.get("INVOCATION_ID", "")
            if not re.fullmatch(r"[a-f0-9]{32}", invocation):
                raise ValueError
            value = {"schema": 1, "scope_epoch": epoch, "pid": os.getpid(), "start_ticks": ticks,
                     "invocation": invocation, "lock_path": str(lock_path), "lock_device": lock.st_dev,
                     "lock_inode": lock.st_ino}
            expected = (_json(value) + "\n").encode()
            if waiting.exists():
                info = waiting.stat(follow_symlinks=False)
                if (info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o400 or info.st_nlink != 1
                    or _private_read(waiting, 65536) != expected):
                    raise ValueError
            else:
                fd = os.open(waiting, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
                with os.fdopen(fd, "wb") as body:
                    body.write(expected)
                    body.flush()
                    os.fsync(body.fileno())
            fd = os.open(waiting.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except (OSError, ValueError, IndexError):
            raise ArchiveBlocked("Root waiting marker creation failed") from None
    deadline = time.monotonic() + 45
    while not pointer.exists():
        source_checkpoint()
        if time.monotonic() >= deadline:
            raise ArchiveBlocked("Fresh root handoff did not arrive")
        time.sleep(0.2)
    raw = _private_read(pointer, 65536)
    info = pointer.stat(follow_symlinks=False)
    if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o400 or info.st_nlink != 1:
        raise ArchiveBlocked("Root handoff pointer identity differs")
    try:
        value = json.loads(raw)
    except ValueError:
        raise ArchiveBlocked("Root handoff pointer is invalid") from None
    if (type(value) is not dict or set(value) != {"schema", "scope_epoch", "path", "sha256"}
        or value["schema"] != 1 or value["scope_epoch"] != epoch
        or type(value["path"]) is not str or not Path(value["path"]).is_absolute()
        or type(value["sha256"]) is not str or not re.fullmatch(r"[a-f0-9]{64}", value["sha256"])):
        raise ArchiveBlocked("Root handoff pointer binding differs")
    return Path(value["path"]), value["sha256"]


def validate_local_scope_bindings(contract, ledger, provider, *, terminating_pin=None):
    """Validate the concrete local half of a pinned consumer scope under jobs.lock."""
    from app.raw_archive.coordination import bound_job_lock

    from .runtime import runtime_sha256

    held_path, held = bound_job_lock()
    if ledger.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='science_registry_import_intent'").fetchone():
        if (ledger.db.execute("SELECT count(*) FROM science_registry_import_intent").fetchone()[0] > 1
            or ledger.db.execute("SELECT 1 FROM science_registry_import_intent WHERE state IS NULL OR state!='complete' OR backup_reference IS NULL LIMIT 1").fetchone()):
            raise ArchiveBlocked("Science registry import requires complete independent backup")

    expected_keys = {
        "schema",
        "environment",
        "box_id",
        "epoch",
        "journal",
        "coordination",
        "source_lease",
        "raw_ledgers",
        "pilot_manifests",
        "restore_descriptors",
        "consumers",
        "source_roster_evidence",
        "runtime_sha256",
        "archive_roster_evidence",
    }
    schema = contract.get("schema") if type(contract) is dict else None
    if schema == 3:
        import socket

        expected_keys = (expected_keys - {"source_roster_evidence", "archive_roster_evidence"}) | {
            "archive_identity", "roster_policies", "dependency_roots"
        }
    if (
        type(contract) is not dict
        or set(contract) != expected_keys
        or schema not in {2, 3}
        or contract["environment"] != "production"
        or type(contract["box_id"]) is not int
        or contract["box_id"] != provider.box_id
        or type(contract["epoch"]) is not str
        or not re.fullmatch(r"[a-f0-9]{32}", contract["epoch"])
        or contract["runtime_sha256"] != runtime_sha256()
    ):
        raise ArchiveBlocked("Complete managed snapshot runtime scope required")

    def exact_file(binding, expected_path):
        if (
            type(binding) is not dict
            or set(binding) != {"path", "device", "inode", "owner", "mode"}
            or type(binding["path"]) is not str
            or any(type(binding[key]) is not int for key in ("device", "inode", "owner", "mode"))
        ):
            raise ArchiveBlocked("Pinned scope file identity required")
        path = Path(binding["path"])
        if (
            not path.is_absolute()
            or path.is_symlink()
            or path.resolve(strict=False) != path
            or path != expected_path
        ):
            raise ArchiveBlocked("Scope file path differs")
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as body:
                info = os.fstat(body.fileno())
                current = path.stat(follow_symlinks=False)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != binding["owner"]
                    or stat.S_IMODE(info.st_mode) != binding["mode"]
                    or (info.st_dev, info.st_ino) != (binding["device"], binding["inode"])
                    or (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino)
                ):
                    raise ArchiveBlocked("Pinned scope file identity differs")
        except OSError:
            raise ArchiveBlocked("Pinned scope file unavailable") from None

    exact_file(contract["coordination"], held_path)
    if (held.st_dev, held.st_ino) != (
        contract["coordination"]["device"],
        contract["coordination"]["inode"],
    ):
        raise ArchiveBlocked("Actual archive jobs lock differs")
    exact_file(contract["journal"], ledger.root / "tiered.sqlite3")
    app_root = Path(__file__).resolve().parents[1]
    expected_consumers = {
        "raw-storage": app_root / "raw_archive/storage.py",
        "raw-pilot": app_root / "raw_archive/pilot.py",
        "raw-recovery": app_root / "raw_archive/recovery.py",
        "raw-catalog": app_root / "raw_archive/wav_catalog.py",
        "snapshot-api": app_root / "raw_archive/snapshot_api.py",
        "tiered-storage": app_root / "tiered_archive/storage.py",
        "tiered-proof": app_root / "tiered_archive/pipeline.py",
    }
    consumers = contract["consumers"]
    if type(consumers) is not list or len(consumers) != len(expected_consumers):
        raise ArchiveBlocked("Complete snapshot consumer roster required")
    seen = set()
    for item in consumers:
        if (
            type(item) is not dict
            or set(item) != {"name", "path", "sha256"}
            or item["name"] not in expected_consumers
            or item["name"] in seen
            or type(item["sha256"]) is not str
            or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"])
        ):
            raise ArchiveBlocked("Snapshot consumer roster differs")
        expected_path = expected_consumers[item["name"]]
        if (
            item["path"] != str(expected_path)
            or hashlib.sha256(expected_path.read_bytes()).hexdigest() != item["sha256"]
        ):
            raise ArchiveBlocked("Snapshot consumer code differs")
        seen.add(item["name"])
    if seen != set(expected_consumers):
        raise ArchiveBlocked("Snapshot consumer roster is incomplete")
    for key in ("raw_ledgers", "pilot_manifests", "restore_descriptors"):
        if type(contract[key]) is not list:
            raise ArchiveBlocked("Pinned external consumer journal roster required")
    from app.raw_archive.remote_lease import require_bound_source_lease

    require_bound_source_lease()
    rows = ledger.db.execute(
        "SELECT s.intent,s.state,s.snapshot FROM managed_snapshot s JOIN managed_snapshot_epoch e ON e.intent=s.intent WHERE e.epoch=? AND s.state!='deleted'",
        (contract["epoch"],),
    ).fetchall()
    current_ids = []
    pending_inventory = None
    for intent, state, raw in rows:
        if raw is None:
            if state != "intent" or not hasattr(provider, "inventory"):
                raise ArchiveBlocked("Unresolved managed snapshot intent prevents roster acceptance")
            if pending_inventory is None:
                pending_inventory, _ = provider.inventory()
            matches = [item for item in pending_inventory if _marker(item, intent)]
            if len(matches) != 1 or matches[0].get("storage_box") != provider.box_id:
                raise ArchiveBlocked("Unresolved managed snapshot intent prevents roster acceptance")
            snapshot = _identity(matches[0])
        else:
            snapshot = json.loads(raw)
        if type(snapshot) is not dict or type(snapshot.get("id")) is not int or snapshot["id"] <= 0:
            raise ArchiveBlocked("Managed snapshot identity is unknown")
        current_ids.append(snapshot["id"])
    if len(set(current_ids)) != len(current_ids):
        raise ArchiveBlocked("Managed snapshot identities are duplicated")
    if schema == 3:
        handoff_path = Path(os.environ.get("TIERED_ARCHIVE_SNAPSHOT_HANDOFF_FILE", ""))
        handoff_sha = os.environ.get("TIERED_ARCHIVE_SNAPSHOT_HANDOFF_SHA256", "")
        if not handoff_sha:
            handoff_path, handoff_sha = _root_handoff_pointer(contract["epoch"])
        if not re.fullmatch(r"[a-f0-9]{64}", handoff_sha):
            raise ArchiveBlocked("Fresh managed snapshot root handoff required")
        handoff_raw = _private_read(handoff_path, 16 * 1024 * 1024)
        metadata = handoff_path.stat(follow_symlinks=False)
        if (metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o400
            or metadata.st_nlink != 1 or hashlib.sha256(handoff_raw).hexdigest() != handoff_sha):
            raise ArchiveBlocked("Fresh managed snapshot root handoff differs")
        for parent in handoff_path.parents:
            info = parent.stat(follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise ArchiveBlocked("Root handoff parent is untrusted")
        try:
            handoff = json.loads(handoff_raw)
        except ValueError:
            raise ArchiveBlocked("Managed snapshot root handoff invalid") from None
        if (type(handoff) is not dict or set(handoff) != {"schema", "scope_epoch", "source", "archive"}
            or handoff["schema"] != 1 or handoff["scope_epoch"] != contract["epoch"]
            or any(type(handoff[role]) is not dict or set(handoff[role]) != {"binding", "evidence"}
                   for role in ("source", "archive"))
            or type(contract["roster_policies"]) is not dict
            or set(contract["roster_policies"]) != {"source", "archive"}):
            raise ArchiveBlocked("Managed snapshot root handoff scope differs")
        source_binding, archive_binding = handoff["source"]["binding"], handoff["archive"]["binding"]
        identity = contract["archive_identity"]
        try:
            local_machine = hashlib.sha256(Path("/etc/machine-id").read_bytes()).hexdigest()
            local_boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        except OSError:
            raise ArchiveBlocked("Live archive host identity unavailable") from None
        if (type(identity) is not dict or set(identity) != {"host", "machine_sha256"}
            or type(archive_binding) is not dict or archive_binding.get("hostname") != identity["host"]
            or archive_binding.get("machine_sha256") != identity["machine_sha256"]
            or identity["host"] != socket.gethostname() or identity["machine_sha256"] != local_machine
            or archive_binding.get("boot_id") != local_boot):
            raise ArchiveBlocked("Archive roster authority differs")
        if (type(contract["dependency_roots"]) is not dict
            or set(contract["dependency_roots"]) != {"source", "archive"}
            or any(type(contract["dependency_roots"][role]) is not list
                   or not contract["dependency_roots"][role] for role in ("source", "archive"))
            or any(binding.get("collector_sha256") != _ROSTER_V2_COLLECTOR
                   for binding in (source_binding, archive_binding))):
            raise ArchiveBlocked("Reviewed dependency roots and roster v2 required")
    else:
        source_binding = contract["source_roster_evidence"]
        archive_binding = contract["archive_roster_evidence"]
    source, source_plan = validate_roster_frame(source_binding, role="source", epoch=contract["epoch"],
                                      current_ids=current_ids, scope_source=contract["source_lease"])
    archive, archive_plan = validate_roster_frame(archive_binding, role="archive", epoch=contract["epoch"],
                                       current_ids=current_ids, scope_source=contract["source_lease"])
    reconcile_root_operations(ledger, epoch=contract["epoch"], current_ids=current_ids,
                              rosters=[source, archive], terminating_pin=terminating_pin)
    if schema == 3:
        source_scope = contract["source_lease"]
        if (type(source_scope) is not dict
            or set(source_scope) != {"host", "machine_sha256", "profile_sha256", "owned_producer_sha256", "runtime_sha256"}
            or source_scope["owned_producer_sha256"] != _REVIEWED_OWNED_PRODUCER
            or source_plan["runtime"]["runtime_sha256"] != source_scope["runtime_sha256"]
            or archive_plan["runtime"]["runtime_sha256"] != contract["runtime_sha256"]):
            raise ArchiveBlocked("Root runtime generations differ from the epoch")
        archive_rules = contract["roster_policies"]["archive"]
        if (type(archive_rules) is not list or sum(
            type(rule) is dict and rule.get("mode") == "archive_worker" for rule in archive_rules
        ) != 1):
            raise ArchiveBlocked("Exactly one live archive snapshot worker is required")
        for role, roster, plan in (("source", source, source_plan), ("archive", archive, archive_plan)):
            if (roster.get("format") != "greenmind.managed-snapshot.consumer-roster.v2"
                or roster.get("dependency_matches") != []
                or plan.get("dependency_roots") != contract["dependency_roots"][role]):
                raise ArchiveBlocked("Root dependency inventory is incomplete or nonempty")
            validate_roster_attestations(
                ledger, roster=roster, plan=plan, policies=contract["roster_policies"][role],
                evidence=handoff[role]["evidence"],
            )
        return
    if source["status"] != "PASS" or archive["status"] != "PASS" or source["reasons"] or archive["reasons"]:
        raise ArchiveBlocked("Root roster consumer and latest coverage requires attested resolution")
    # A PASS frame is not itself proof of loaded modules or a safe exclusion.
    # A future attestation-aware collector/validator must resolve every unit.
    raise ArchiveBlocked("Root roster import and exclusion attestations are not yet validated")


def reconcile_scope(ledger, provider, *, contract, contract_sha256, activate=False):
    """Transition an epoch only after both fresh root rosters reconcile under the lock."""
    from app.raw_archive.coordination import bound_job_lock

    bound_job_lock()
    if (type(contract) is not dict or contract.get("schema") != 3
        or not re.fullmatch(r"[a-f0-9]{64}", str(contract_sha256))):
        raise ArchiveBlocked("Pinned schema 3 scope contract required")
    initialize_managed(ledger.db)
    initialize_snapshot_pins(ledger.db)
    ledger.db.commit()
    try:
        ledger.db.execute("BEGIN IMMEDIATE")
        scope = ledger.db.execute(
            "SELECT epoch,contract_sha256,recovery_state FROM managed_snapshot_scope WHERE id=1"
        ).fetchone()
        if activate:
            if scope or ledger.db.execute(
                "SELECT 1 FROM managed_snapshot WHERE state!='deleted' LIMIT 1"
            ).fetchone() or ledger.db.execute(
                "SELECT 1 FROM managed_snapshot_pin WHERE state!='released' LIMIT 1"
            ).fetchone():
                raise ArchiveBlocked("New scope requires no existing active managed operations")
            ledger.db.execute(
                "INSERT INTO managed_snapshot_scope VALUES(1,?,?,'needs_reconcile')",
                (contract.get("epoch"), contract_sha256),
            )
        elif not scope or scope != (contract.get("epoch"), contract_sha256, "needs_reconcile"):
            raise ArchiveBlocked("Restored managed scope requiring reconciliation required")
        validate_local_scope_bindings(contract, ledger, provider)
        ledger.db.execute("UPDATE managed_snapshot_scope SET recovery_state='current' WHERE id=1")
        ledger.db.commit()
    except BaseException:
        ledger.db.rollback()
        raise


def check_external_dependencies(victim, _verified, *, ledger=None, provider=None):
    """Inspect pinned raw journals; untracked restore sessions still force HOLD."""
    if ledger is not None:
        from app.raw_archive.coordination import bound_job_lock

        bound_job_lock()
        scope = ledger.db.execute(
            "SELECT epoch,recovery_state,contract_sha256 FROM managed_snapshot_scope WHERE id=1"
        ).fetchone()
        if not scope or scope[1] != "current":
            raise ArchiveBlocked("Current managed snapshot scope required")
        epoch = ledger.db.execute(
            "SELECT e.epoch FROM managed_snapshot_epoch e JOIN managed_snapshot s ON s.intent=e.intent WHERE s.snapshot IS NOT NULL AND json_extract(s.snapshot,'$.id')=?",
            (victim["id"],),
        ).fetchall()
        if len(epoch) != 1 or epoch[0][0] != scope[0]:
            raise ArchiveBlocked("Snapshot does not belong to current managed epoch")
        for (state,) in ledger.db.execute(
            "SELECT state FROM managed_snapshot_pin WHERE snapshot_id=?", (victim["id"],)
        ):
            if state not in {"active", "released", "uncertain"}:
                raise ArchiveBlocked("Unknown snapshot pin state")
            if state in {"active", "uncertain"}:
                raise ArchiveBlocked("Managed snapshot has active or uncertain pins")
    path = Path(os.environ.get("TIERED_ARCHIVE_SNAPSHOT_DEPENDENCIES_FILE", ""))
    pin = os.environ.get("TIERED_ARCHIVE_SNAPSHOT_DEPENDENCIES_SHA256", "")
    if not path.is_absolute() or path.is_symlink() or not re.fullmatch(r"[a-f0-9]{64}", pin):
        raise ArchiveBlocked("Pinned external snapshot dependency catalogue required")
    try:
        raw = _private_read(path, 1024 * 1024)
        if hashlib.sha256(raw).hexdigest() != pin:
            raise ArchiveBlocked("External snapshot dependency catalogue differs")
        config = json.loads(raw)
    except (OSError, ValueError):
        raise ArchiveBlocked("External snapshot dependency catalogue unavailable") from None
    if (
        type(config) is not dict
        or config.get("schema") not in {1, 2, 3}
        or (config.get("schema") == 1 and set(config) != {"schema", "environment", "raw_ledgers"})
        or config["environment"] != "production"
        or type(config["raw_ledgers"]) is not list
        or not 1 <= len(config["raw_ledgers"]) <= 16
    ):
        raise ArchiveBlocked("External raw archive journal bindings are unknown")
    if config["schema"] in {2, 3}:
        if (
            ledger is None
            or provider is None
            or config.get("box_id") != provider.box_id
            or config.get("epoch") != scope[0]
            or pin != scope[2]
        ):
            raise ArchiveBlocked("Managed snapshot dependency scope differs")
        validate_local_scope_bindings(config, ledger, provider)
    seen = set()
    for item in config["raw_ledgers"]:
        fields = (
            {"path", "device", "inode"}
            if config["schema"] == 1
            else {"path", "device", "inode", "owner", "mode"}
        )
        if (
            type(item) is not dict
            or set(item) != fields
            or type(item["path"]) is not str
            or type(item["device"]) is not int
            or type(item["inode"]) is not int
            or (
                config["schema"] in {2, 3}
                and (type(item["owner"]) is not int or type(item["mode"]) is not int)
            )
        ):
            raise ArchiveBlocked("Pinned raw archive journal identity required")
        journal = Path(item["path"])
        if (
            not journal.is_absolute()
            or journal.is_symlink()
            or journal.resolve(strict=False) != journal
            or str(journal) in seen
        ):
            raise ArchiveBlocked("Raw archive journal path is unknown")
        seen.add(str(journal))
        try:
            fd = os.open(journal, os.O_RDONLY | os.O_NOFOLLOW)
            metadata = os.fstat(fd)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_dev != item["device"]
                or metadata.st_ino != item["inode"]
                or (
                    config["schema"] in {2, 3}
                    and (
                        metadata.st_uid != item["owner"]
                        or stat.S_IMODE(metadata.st_mode) != item["mode"]
                    )
                )
            ):
                os.close(fd)
                raise ArchiveBlocked("Raw archive journal identity differs")

            def sidecars(journal=journal, owner=metadata.st_uid):
                result = []
                for suffix in ("-wal", "-shm"):
                    sidecar = Path(str(journal) + suffix)
                    try:
                        info = sidecar.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        result.append(None)
                        continue
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or info.st_uid not in {0, owner}
                        or info.st_mode & 0o007
                    ):
                        raise ArchiveBlocked("Raw archive journal sidecar is untrusted")
                    result.append((info.st_dev, info.st_ino))
                return result

            try:
                sidecars_before = sidecars()
            except BaseException:
                os.close(fd)
                raise
            try:
                db = sqlite3.connect(journal.as_uri() + "?mode=ro", uri=True, timeout=2)
            except BaseException:
                os.close(fd)
                raise
            try:
                db.execute("BEGIN")
                for state, raw_receipt in db.execute("SELECT state,receipt FROM archive"):
                    if state not in {"copying", "verified", "deleting", "evicted"}:
                        raise ArchiveBlocked("Unknown raw archive journal state")
                    receipt = json.loads(raw_receipt)
                    if (
                        type(receipt) is not dict
                        or type(receipt.get("recording")) is not dict
                        or type(receipt.get("snapshot")) is not dict
                        or type(receipt.get("remote_key")) is not str
                        or type(receipt.get("destination")) is not str
                    ):
                        raise ArchiveBlocked("Unknown raw archive receipt")
                    snapshot = receipt.get("provider_snapshot")
                    if state == "deleting" and snapshot is None:
                        raise ArchiveBlocked("Raw archive deletion snapshot binding is unknown")
                    if snapshot is not None and (
                        type(snapshot) is not dict
                        or type(snapshot.get("provider_id")) is not int
                        or snapshot.get("provider_id", 0) <= 0
                        or type(snapshot.get("name")) is not str
                        or not re.fullmatch(
                            r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", snapshot.get("name", "")
                        )
                        or snapshot.get("provider_id") == victim["id"]
                        or snapshot.get("name") == victim["name"]
                    ):
                        raise ArchiveBlocked("Raw archive snapshot dependency is active or unknown")
                after = os.fstat(fd)
                current = journal.stat(follow_symlinks=False)
                if (
                    (after.st_dev, after.st_ino) != (metadata.st_dev, metadata.st_ino)
                    or (
                        current.st_dev,
                        current.st_ino,
                    )
                    != (metadata.st_dev, metadata.st_ino)
                    or sidecars() != sidecars_before
                ):
                    raise ArchiveBlocked("Raw archive journal identity changed")
            finally:
                db.close()
                os.close(fd)
        except (OSError, sqlite3.Error, ValueError, TypeError):
            raise ArchiveBlocked("Raw archive journal cannot be inspected") from None
    if config["schema"] == 3:
        if config["pilot_manifests"] or config["restore_descriptors"]:
            raise ArchiveBlocked("Nonempty pilot or restore inventory requires an explicit resolver")
        return
    # Restore sessions are journalled in separate PostgreSQL targets, while
    # pilot manifests are separate operator files. No exhaustive pinned
    # inventory or common lease exists yet; no SQLite scan can prove absence.
    raise ArchiveBlocked("External restore and pilot dependency coverage is unknown")


class SnapshotReadbackPending(dict):
    """Explicit deferred status that existing scheduler treats as no snapshot."""

    def __bool__(self):
        return False


class _ReadbackDeferred(Exception):
    pass


def _readback_inventory(copied, snapshot, store, checkpoint):
    """Build a deterministic inventory from the frozen, restored catalogue."""
    from . import registry_v5

    environment = snapshot["environment"]
    items, journals, backup_sources = [], {}, set()
    for (raw,) in copied.db.execute("SELECT receipt FROM bundle ORDER BY id"):
        checkpoint()
        receipt = json.loads(raw)
        candidate = candidate_from_json(receipt["source"])
        if receipt["destination"] != store.identity or utc(receipt["published_at"]) > utc(snapshot["created"]):
            raise ArchiveBlocked("Snapshot catalogue destination or publication differs")
        if (candidate.dataset == "database_backup" and candidate.source in {"gateway", "direct"}
            and utc(snapshot["created"]) - timedelta(hours=12) <= candidate.end <= utc(snapshot["created"])):
            backup_sources.add(candidate.source)
        for number, ref in enumerate([receipt["manifest"], *receipt["files"]]):
            item = {"kind": "bundle-manifest" if number == 0 else "bundle-file", "reference": ref}
            if number == 0:
                item.update(candidate=candidate_json(candidate), identity=candidate.identity, files=receipt["files"])
            items.append(item)
    if not items:
        raise ArchiveBlocked("Empty snapshot catalogue cannot prove backup coverage")
    if environment == "production" and backup_sources != {"gateway", "direct"}:
        raise ArchiveBlocked("Snapshot catalogue lacks both recent production backups")
    for table in registry_v5.TABLES:
        exists = copied.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        rows = []
        if exists:
            registry_v5.schema(copied.db, table)
            for row in copied.db.execute("SELECT * FROM " + table + " ORDER BY 1,2"):
                checkpoint()
                values = list(row)
                registry_v5.validate(table, values, environment, copied)
                rows.append(values)
                if len(rows) > 100000:
                    raise ArchiveBlocked("Snapshot V5 journal exceeds budget")
                if table in {"science_v3_part", "science_v3_whole"}:
                    if values[2] != "verified":
                        raise ArchiveBlocked("Unresolved science snapshot publication")
                    reference = json.loads(values[3])
                    if table == "science_v3_part":
                        binding = json.loads(values[1])
                        if binding["destination"] != store.identity:
                            raise ArchiveBlocked("Science snapshot destination differs")
                        items.append({"kind": "science-part", "reference": reference, "binding": binding})
                    else:
                        items.append({"kind": "science-whole", "reference": reference, "whole_sha256": values[1]})
        journals[table] = {"present": bool(exists), "rows": rows}
    inventory = {"items": items, "journals": journals}
    encoded = _json(inventory).encode()
    if len(encoded) > 16 * 1024**2:
        raise ArchiveBlocked("Snapshot readback inventory exceeds budget")
    return inventory, hashlib.sha256(encoded).hexdigest()


def _verify_science_item(path, item, key, inventory, checkpoint, folder):
    from app.raw_archive import scientific_capture, scientific_parts, scientific_v3

    from .backup import decrypt

    opened = Path(folder) / "science-opened"
    reference = item["reference"]
    decrypt(path, opened, key, reference["plaintext_sha256"], checkpoint=checkpoint)
    try:
        if item["kind"] == "science-part":
            part = item["binding"]["part"]
            columns = scientific_capture.roster()[part["table"]]
            scientific_v3._typed(opened, columns, part["rows"], checkpoint)
            return
        if opened.stat().st_size > 16 * 1024**2:
            raise ArchiveBlocked("Science whole manifest exceeds budget")
        raw = opened.read_bytes()
        whole = json.loads(raw)
        fields = {"format", "capture", "capture_sha256", "index", "index_sha256", "destination",
                  "references", "native_restore_verified", "parent_fk_closure_proven", "cross_database_atomic"}
        if (type(whole) is not dict or set(whole) != fields or whole["format"] != "greenmind.science-whole.v3"
            or whole["native_restore_verified"] is not False or whole["parent_fk_closure_proven"] is not False
            or whole["cross_database_atomic"] is not False or whole["destination"] != inventory["destination"]
            or hashlib.sha256((_json(whole["capture"]) + "\n").encode()).hexdigest() != whole["capture_sha256"]
            or hashlib.sha256((_json(whole["index"]) + "\n").encode()).hexdigest() != whole["index_sha256"]):
            raise ArchiveBlocked("Science whole snapshot identity differs")
        scientific_parts.validate_index(whole["capture"], whole["index"], whole["capture_sha256"])
        bound = [entry for entry in inventory["items"] if entry["kind"] == "science-part"
                 and entry["binding"]["capture_sha256"] == whole["capture_sha256"]
                 and entry["binding"]["index_sha256"] == whole["index_sha256"]]
        bound.sort(key=lambda entry: entry["binding"]["position"])
        if (len(bound) != len(whole["index"]["parts"])
            or [entry["binding"]["position"] for entry in bound] != list(range(len(bound)))
            or [entry["binding"]["part"] for entry in bound] != whole["index"]["parts"]
            or [entry["reference"] for entry in bound] != whole["references"]):
            raise ArchiveBlocked("Science whole inventory does not match journalled parts")
    finally:
        if opened.exists():
            opened.unlink()


_READBACK_CACHE_MAX_BYTES = 64 * 1024**2
_READBACK_DISK_RESERVE = 2499805184


def _readback_cache_root(ledger):
    root = ledger.root
    if os.getenv("TIERED_ARCHIVE_ENVIRONMENT") == "production" and os.geteuid() != 0:
        raise ArchiveBlocked("Root-owned production readback cache required")
    if not root.is_absolute() or root.resolve(strict=False) != root:
        raise ArchiveBlocked("Canonical readback cache ancestor required")
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ArchiveBlocked("Private readback cache ancestor required")
    parent = root / "snapshot-readback"
    parent.mkdir(mode=0o700, exist_ok=True)
    info = parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ArchiveBlocked("Private readback cache root required")
    return parent


def _cache_owner_marker(intent, binding_sha):
    return (_json({"schema": 1, "intent": intent, "binding_sha256": binding_sha}) + "\n").encode()


def _cache_safe_to_clean(ledger, intent):
    row = ledger.db.execute("SELECT state,snapshot,proof FROM managed_snapshot WHERE intent=?", (intent,)).fetchone()
    if not row or row[0] != "deleted":
        return False
    try:
        identity = json.loads(row[1])["id"]
        proof = json.loads(row[2])
        if (proof["snapshot_id"] != identity or _completed_readback(ledger, intent, proof) is None
            or not ledger.db.execute("SELECT 1 FROM managed_snapshot_action WHERE intent=? AND operation='delete' AND action_id>0", (intent,)).fetchone()):
            return False
        for pin_id, state, completed_at, completion_sha in ledger.db.execute(
            "SELECT id,state,completed_at,completion_sha256 FROM managed_snapshot_pin WHERE intent=? OR snapshot_id=?", (intent, identity)
        ):
            if (state != "released" or not completed_at or not re.fullmatch(r"[a-f0-9]{64}", completion_sha or "")
                or not ledger.db.execute("SELECT 1 FROM managed_snapshot_pin_event WHERE pin_id=? AND state='released' AND evidence=?", (pin_id, completion_sha)).fetchone()):
                return False
            utc(completed_at)
        for (raw,) in ledger.db.execute("SELECT receipt FROM bundle"):
            if json.loads(raw).get("snapshot", {}).get("provider_id") == identity:
                return False
    except (KeyError, TypeError, ValueError):
        raise ArchiveBlocked("Unknown readback cache references") from None
    return True


def _cache_inventory(ledger, parent):
    ancestor = ledger.root.lstat()
    root = parent.lstat()
    if (not stat.S_ISDIR(ancestor.st_mode) or not stat.S_ISDIR(root.st_mode)
        or ancestor.st_uid != os.geteuid() or root.st_uid != os.geteuid()
        or stat.S_IMODE(ancestor.st_mode) != 0o700 or stat.S_IMODE(root.st_mode) != 0o700):
        raise ArchiveBlocked("Readback cache ancestors changed")
    total, owners = 0, {}
    for directory in parent.iterdir():
        if not re.fullmatch(r"[a-f0-9]{32}", directory.name):
            raise ArchiveBlocked("Unknown readback cache directory")
        row = ledger.db.execute("SELECT binding_sha256,state,device,inode,cleanup_manifest FROM managed_snapshot_cache_owner WHERE intent=?", (directory.name,)).fetchone()
        info = directory.lstat()
        if (not row or row[1] not in {"active", "cleaning"} or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700
            or (row[2] is not None and (info.st_dev, info.st_ino) != (row[2], row[3]))):
            raise ArchiveBlocked("Unknown readback cache ownership")
        if row[2] is None:
            raise ArchiveBlocked("Incomplete readback cache ownership")
        marker = directory / ".owner.json"
        manifest = json.loads(row[4]) if row[1] == "cleaning" and row[4] else None
        if marker.exists():
            if _private_read(marker, 1024) != _cache_owner_marker(directory.name, row[0]):
                raise ArchiveBlocked("Readback cache owner marker differs")
        elif not (manifest is not None and all(not (directory / item["name"]).exists() for item in manifest if item["name"] != ".owner.json")):
            raise ArchiveBlocked("Readback cache owner marker missing")
        expected = {r[0]: r[1] for r in ledger.db.execute("SELECT sha256,bytes FROM managed_snapshot_cache_file WHERE intent=?", (directory.name,))}
        entries = []
        for path in directory.iterdir():
            st = path.lstat()
            if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid()
                or stat.S_IMODE(st.st_mode) != 0o400 or st.st_nlink != 1):
                raise ArchiveBlocked("Unsafe readback cache file")
            if path.name != ".owner.json" and (path.name not in expected or st.st_size != expected[path.name]):
                raise ArchiveBlocked("Unknown readback cache file")
            if path.name != ".owner.json" and hashlib.sha256(_private_read(path, 1024 * 1024 + 1)).hexdigest() != path.name:
                raise ArchiveBlocked("Readback cache file differs")
            entries.append({"name": path.name, "device": st.st_dev, "inode": st.st_ino, "size": st.st_size,
                            "mtime_ns": st.st_mtime_ns, "ctime_ns": st.st_ctime_ns})
            total += st.st_size
        owners[directory.name] = (row, info, entries)
    after_ancestor, after_root = ledger.root.lstat(), parent.lstat()
    if ((ancestor.st_dev, ancestor.st_ino) != (after_ancestor.st_dev, after_ancestor.st_ino)
        or (root.st_dev, root.st_ino) != (after_root.st_dev, after_root.st_ino)):
        raise ArchiveBlocked("Readback cache ancestors changed")
    if total > _READBACK_CACHE_MAX_BYTES:
        raise ArchiveBlocked("Readback cache exceeds global budget")
    return total, owners


def _cache_cleanup(ledger, parent, intent, owner):
    from app.raw_archive.coordination import bound_job_lock

    bound_job_lock()
    if not _cache_safe_to_clean(ledger, intent):
        raise ArchiveBlocked("Readback cache still referenced")
    row, info, entries = owner
    if row[1] == "active":
        entries.sort(key=lambda item: (item["name"] == ".owner.json", item["name"]))
        manifest = _json(entries)
        with ledger.db:
            ledger.db.execute("UPDATE managed_snapshot_cache_owner SET state='cleaning',cleanup_manifest=? WHERE intent=? AND state='active'", (manifest, intent))
    else:
        manifest = row[4]
    try:
        items = json.loads(manifest)
        if (type(items) is not list or any(type(item) is not dict or set(item) != {"name", "device", "inode", "size", "mtime_ns", "ctime_ns"}
            or (item["name"] != ".owner.json" and not re.fullmatch(r"[a-f0-9]{64}", item["name"])) for item in items)):
            raise ArchiveBlocked("Invalid readback cleanup manifest")
        parent_info = parent.lstat()
        pfd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        parent_open = os.fstat(pfd)
        if (parent_open.st_dev, parent_open.st_ino) != (parent_info.st_dev, parent_info.st_ino):
            os.close(pfd)
            raise ArchiveBlocked("Readback cleanup ancestor changed")
        try:
            fd = os.open(intent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pfd)
        except BaseException:
            os.close(pfd)
            raise
        try:
            directory = os.fstat(fd)
            if (directory.st_dev, directory.st_ino) != (info.st_dev, info.st_ino):
                raise ArchiveBlocked("Readback cleanup directory changed")
            for item in items:
                if not _cache_safe_to_clean(ledger, intent):
                    raise ArchiveBlocked("Readback cache became referenced")
                try:
                    current = os.stat(item["name"], dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if ((current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns)
                    != (item["device"], item["inode"], item["size"], item["mtime_ns"], item["ctime_ns"])
                    or not stat.S_ISREG(current.st_mode) or current.st_uid != os.geteuid()
                    or stat.S_IMODE(current.st_mode) != 0o400 or current.st_nlink != 1):
                    raise ArchiveBlocked("Readback cleanup file changed")
                os.unlink(item["name"], dir_fd=fd)
                os.fsync(fd)
            if os.listdir(fd):
                raise ArchiveBlocked("Unknown readback cleanup file")
        except BaseException:
            os.close(pfd)
            raise
        finally:
            os.close(fd)
        try:
            current = os.stat(intent, dir_fd=pfd, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise ArchiveBlocked("Readback cleanup directory changed")
            os.rmdir(intent, dir_fd=pfd)
            os.fsync(pfd)
        finally:
            os.close(pfd)
        with ledger.db:
            ledger.db.execute("UPDATE managed_snapshot_cache_owner SET state='cleaned' WHERE intent=? AND state='cleaning'", (intent,))
    except (KeyError, TypeError, ValueError, OSError):
        raise ArchiveBlocked("Readback cache cleanup interrupted") from None


def _cache_reserve(ledger, parent, size, current_intent):
    for intent, state in ledger.db.execute("SELECT intent,state FROM managed_snapshot_cache_owner WHERE state IN ('active','cleaning')").fetchall():
        if not (parent / intent).exists():
            if state != "cleaning" or not _cache_safe_to_clean(ledger, intent):
                raise ArchiveBlocked("Readback cache owner directory missing")
            from app.raw_archive.coordination import bound_job_lock
            bound_job_lock()
            with ledger.db:
                ledger.db.execute("UPDATE managed_snapshot_cache_owner SET state='cleaned' WHERE intent=? AND state='cleaning'", (intent,))
    total, owners = _cache_inventory(ledger, parent)
    for intent, owner in sorted(owners.items(), key=lambda pair: pair[0]):
        if owner[0][1] == "cleaning":
            _cache_cleanup(ledger, parent, intent, owner)
            total, owners = _cache_inventory(ledger, parent)
    if shutil.disk_usage(parent).free < _READBACK_DISK_RESERVE + size:
        raise ArchiveBlocked("Archive disk safety reserve exhausted")
    while total + size > _READBACK_CACHE_MAX_BYTES:
        eligible = [intent for intent in owners if intent != current_intent and _cache_safe_to_clean(ledger, intent)]
        if not eligible:
            raise ArchiveBlocked("Readback cache budget exhausted")
        eligible.sort(key=lambda item: ledger.db.execute("SELECT started_at FROM managed_snapshot_readback WHERE intent=?", (item,)).fetchone()[0])
        _cache_cleanup(ledger, parent, eligible[0], owners[eligible[0]])
        total, owners = _cache_inventory(ledger, parent)


def _verify_resumable(provider, snapshot, reference, intent, ledger, store, checkpoint, now, *, max_bytes, expected_state="bound"):
    """Verify at most one bounded remote object, retaining only auditable local evidence."""
    if type(max_bytes) is not int or not 1 <= max_bytes <= 64 * 1024**2:
        raise ArchiveBlocked("Bounded snapshot readback byte budget required")
    initialize_snapshot_readback(ledger.db)
    ledger.db.commit()
    moment = utc(now())
    live = provider.read(snapshot["id"])
    if _identity(live) != _identity(snapshot) or not _marker(live, intent):
        raise ArchiveBlocked("Snapshot identity changed before readback")
    provider.require_idle_actions()
    journal_ref = managed_catalog_reference(live)
    if journal_ref["key"].split("/", 1)[0] != reference["key"].split("/", 1)[0]:
        raise ArchiveBlocked("Managed snapshot recovery environment differs")
    def check_binding():
        saved = ledger.db.execute("SELECT state,catalog,snapshot FROM managed_snapshot WHERE intent=?", (intent,)).fetchone()
        if (not saved or saved[0] != expected_state or json.loads(saved[1]) != reference
            or json.loads(saved[2]) != _identity(snapshot)):
            raise ArchiveBlocked("Bound snapshot catalogue changed during readback")

    check_binding()
    key_sha = os.getenv("TIERED_ARCHIVE_BACKUP_KEY_SHA256", "")
    binding = {"intent": intent, "snapshot": _identity(snapshot), "reference": reference,
               "journal_reference": journal_ref, "destination": store.identity}
    binding_sha = hashlib.sha256(_json(binding).encode()).hexdigest()
    with ledger.db:
        previous = ledger.db.execute("SELECT binding_sha256,started_at FROM managed_snapshot_readback WHERE intent=?", (intent,)).fetchone()
        if previous and previous[0] != binding_sha:
            raise ArchiveBlocked("Snapshot readback immutable binding changed")
        if previous and not timedelta(0) <= moment - utc(previous[1]) <= timedelta(hours=24):
            ledger.db.execute("DELETE FROM managed_snapshot_readback_item WHERE intent=?", (intent,))
            ledger.db.execute("DELETE FROM managed_snapshot_readback_cache WHERE intent=?", (intent,))
            ledger.db.execute("DELETE FROM managed_snapshot_readback_key WHERE intent=?", (intent,))
            ledger.db.execute("UPDATE managed_snapshot_readback SET inventory_sha256=NULL,started_at=? WHERE intent=?", (moment.isoformat(), intent))
        elif previous is None:
            ledger.db.execute("INSERT INTO managed_snapshot_readback VALUES(?,?,NULL,?)", (intent, binding_sha, moment.isoformat()))
    if not re.fullmatch(r"[a-f0-9]{32}", intent):
        raise ArchiveBlocked("Owned readback cache identity required")
    parent = _readback_cache_root(ledger)
    cache = parent / intent
    owner = ledger.db.execute("SELECT binding_sha256,state,device,inode FROM managed_snapshot_cache_owner WHERE intent=?", (intent,)).fetchone()
    if owner is None:
        if cache.exists() or cache.is_symlink():
            raise ArchiveBlocked("Unknown legacy readback cache")
        _cache_reserve(ledger, parent, len(_cache_owner_marker(intent, binding_sha)), intent)
        with ledger.db:
            ledger.db.execute("INSERT INTO managed_snapshot_cache_owner VALUES(?,?,'active',NULL,NULL,NULL)", (intent, binding_sha))
        owner = (binding_sha, "active", None, None)
    if owner[0] != binding_sha or owner[1] != "active":
        raise ArchiveBlocked("Readback cache owner binding differs")
    cache.mkdir(mode=0o700, exist_ok=True)
    info = cache.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700
        or (owner[2] is not None and (info.st_dev, info.st_ino) != (owner[2], owner[3]))):
        raise ArchiveBlocked("Private snapshot readback cache required")
    marker = cache / ".owner.json"
    if not marker.exists():
        if owner[2] is not None or any(cache.iterdir()):
            raise ArchiveBlocked("Unknown legacy readback cache")
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
        with os.fdopen(fd, "wb") as file:
            file.write(_cache_owner_marker(intent, binding_sha))
            file.flush()
            os.fsync(file.fileno())
        fd = os.open(cache, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    elif _private_read(marker, 1024) != _cache_owner_marker(intent, binding_sha):
        raise ArchiveBlocked("Readback cache owner marker differs")
    if owner[2] is None:
        with ledger.db:
            ledger.db.execute("UPDATE managed_snapshot_cache_owner SET device=?,inode=? WHERE intent=? AND device IS NULL", (info.st_dev, info.st_ino, intent))
    _cache_reserve(ledger, parent, 0, intent)
    transfer = {"used": False}

    class SnapshotStore:
        def download(self, key, path, size):
            checkpoint()
            if (type(key) is not str or type(size) is not int or not 0 < size <= 1024 * 1024 + 1):
                raise ArchiveBlocked("Bounded snapshot catalogue object required")
            name = key.rsplit("/", 1)[-1]
            match = re.fullmatch(r"([a-f0-9]{64})\.(?:json|gz)", name)
            if not match:
                raise ArchiveBlocked("Snapshot catalogue content address required")
            sha = match.group(1)
            cache_path = cache / sha
            record = ledger.db.execute(
                "SELECT bytes,sha256,verified_at FROM managed_snapshot_readback_cache WHERE intent=? AND object_key=?",
                (intent, key),
            ).fetchone()
            if record:
                if record[:2] != (size, sha) or not timedelta(0) <= moment - utc(record[2]) <= timedelta(hours=24):
                    raise ArchiveBlocked("Snapshot cached catalogue binding differs")
                if hashlib.sha256(_private_read(cache_path, 64 * 1024**2)).hexdigest() != sha:
                    raise ArchiveBlocked("Snapshot cached catalogue changed")
            else:
                if transfer["used"] or size > max_bytes:
                    raise _ReadbackDeferred
                transfer["used"] = True
                checkpoint()
                _cache_reserve(ledger, parent, 0 if cache_path.exists() else size, intent)
                with ledger.db:
                    prior_file = ledger.db.execute("SELECT bytes FROM managed_snapshot_cache_file WHERE intent=? AND sha256=?", (intent, sha)).fetchone()
                    if prior_file and prior_file[0] != size:
                        raise ArchiveBlocked("Readback cache manifest differs")
                    ledger.db.execute("INSERT OR IGNORE INTO managed_snapshot_cache_file VALUES(?,?,?)", (intent, sha, size))
                with tempfile.TemporaryDirectory(dir=ledger.root) as temporary:
                    fetched = Path(temporary) / "object"
                    store.download(key, fetched, size, snapshot=snapshot["name"])
                    if fetched.stat().st_size != size or hashlib.sha256(fetched.read_bytes()).hexdigest() != sha:
                        raise ArchiveBlocked("Snapshot catalogue readback differs")
                    os.chmod(fetched, 0o400)
                    with fetched.open("rb") as body:
                        os.fsync(body.fileno())
                    if cache_path.exists():
                        if hashlib.sha256(_private_read(cache_path, 64 * 1024**2)).hexdigest() != sha:
                            raise ArchiveBlocked("Snapshot cached catalogue changed")
                    else:
                        os.link(fetched, cache_path)
                    fd = os.open(cache, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                with ledger.db:
                    ledger.db.execute("INSERT INTO managed_snapshot_readback_cache VALUES(?,?,?,?,?)", (intent, key, size, sha, moment.isoformat()))
            shutil.copyfile(cache_path, path)

    try:
        with tempfile.TemporaryDirectory(dir=ledger.root) as folder:
            restored_journal = Ledger(Path(folder) / "journal")
            try:
                restore(journal_ref, restored_journal, SnapshotStore(), checkpoint=checkpoint,
                        expected_environment=journal_ref["key"].split("/", 1)[0])
                row = restored_journal.db.execute("SELECT state,catalog FROM managed_snapshot WHERE intent=?", (intent,)).fetchone()
                if not row or row[0] != "intent" or json.loads(row[1]) != reference:
                    raise ArchiveBlocked("Snapshot ownership journal readback differs")
            finally:
                restored_journal.close()
            copied = Ledger(Path(folder) / "catalog")
            try:
                restore(reference, copied, SnapshotStore(), checkpoint=checkpoint,
                        expected_environment=reference["key"].split("/", 1)[0])
                inventory, _ = _readback_inventory(copied, {**snapshot, "environment": reference["key"].split("/", 1)[0]}, store, checkpoint)
            finally:
                copied.close()
            inventory["destination"] = store.identity
            science = any(item["kind"].startswith("science-") for item in inventory["items"])
            key = None
            if science:
                if not re.fullmatch(r"[a-f0-9]{64}", key_sha):
                    raise ArchiveBlocked("Pinned archive encryption key required for science readback")
                key = _private_read(os.environ.get("TIERED_ARCHIVE_BACKUP_KEY_FILE", ""), 33)
                if len(key) != 32 or hashlib.sha256(key).hexdigest() != key_sha:
                    raise ArchiveBlocked("Archive encryption key binding differs")
            if science:
                from .scientific_registry_import import verify_provenance_evidence
                for row in inventory["journals"]["science_v3_import_provenance"]["rows"]:
                    verify_provenance_evidence(json.loads(row[3]), key)
            inventory["backup_key_sha256"] = key_sha if science else None
            inventory_sha = hashlib.sha256(_json(inventory).encode()).hexdigest()
            with ledger.db:
                prior = ledger.db.execute("SELECT inventory_sha256 FROM managed_snapshot_readback WHERE intent=?", (intent,)).fetchone()[0]
                if prior is not None and prior != inventory_sha:
                    raise ArchiveBlocked("Frozen snapshot inventory changed")
                ledger.db.execute("UPDATE managed_snapshot_readback SET inventory_sha256=? WHERE intent=?", (inventory_sha, intent))
                ledger.db.execute("INSERT INTO managed_snapshot_readback_key VALUES(?,?) ON CONFLICT(intent) DO UPDATE SET key_sha256=excluded.key_sha256", (intent, key_sha if science else None))
            completed = ledger.db.execute(
                "SELECT position,item_sha256,verified_at FROM managed_snapshot_readback_item WHERE intent=? ORDER BY position", (intent,)
            ).fetchall()
            if len(completed) > len(inventory["items"]):
                raise ArchiveBlocked("Snapshot readback progress exceeds inventory")
            for position, saved_sha, checked_at in completed:
                if (position >= len(inventory["items"]) or position < 0
                    or saved_sha != hashlib.sha256(_json(inventory["items"][position]).encode()).hexdigest()
                    or not timedelta(0) <= moment - utc(checked_at) <= timedelta(hours=24)):
                    raise ArchiveBlocked("Snapshot readback progress differs")
            if [row[0] for row in completed] != list(range(len(completed))):
                raise ArchiveBlocked("Snapshot readback progress has gaps")
            if len(completed) < len(inventory["items"]):
                item = inventory["items"][len(completed)]
                ref = item["reference"]
                if (type(ref) is not dict or type(ref.get("bytes")) is not int
                    or not 0 < ref["bytes"] <= 64 * 1024**2):
                    raise ArchiveBlocked("Snapshot object exceeds readback budget")
                if transfer["used"] or ref["bytes"] > max_bytes:
                    raise _ReadbackDeferred
                transfer["used"] = True
                checkpoint()
                path = Path(folder) / "object"
                store.download(ref["key"], path, ref["bytes"], snapshot=snapshot["name"])
                if item["kind"].startswith("science-"):
                    if path.stat().st_size != ref["bytes"] or checksum(path) != ref["sha256"]:
                        raise ArchiveBlocked("Science snapshot ciphertext differs")
                    _verify_science_item(path, item, key, inventory, checkpoint, folder)
                else:
                    verify_file(path, ref, checkpoint)
                    if item["kind"] == "bundle-manifest":
                        manifest = json.loads(path.read_text())
                        if (manifest.get("identity") != item["identity"] or manifest.get("candidate") != item["candidate"]
                            or manifest.get("files") != item["files"] or manifest.get("policy") != POLICY_SHA256):
                            raise ArchiveBlocked("Snapshot bundle manifest differs")
                checkpoint()
                live = provider.read(snapshot["id"])
                if _identity(live) != _identity(snapshot) or not _marker(live, intent) or managed_catalog_reference(live) != journal_ref:
                    raise ArchiveBlocked("Snapshot identity changed during readback")
                provider.require_idle_actions()
                check_binding()
                if science and hashlib.sha256(_private_read(os.environ.get("TIERED_ARCHIVE_BACKUP_KEY_FILE", ""), 33)).hexdigest() != key_sha:
                    raise ArchiveBlocked("Archive encryption key changed during readback")
                with ledger.db:
                    ledger.db.execute("INSERT INTO managed_snapshot_readback_item VALUES(?,?,?,?)",
                                      (intent, len(completed), hashlib.sha256(_json(item).encode()).hexdigest(), moment.isoformat()))
                completed.append((len(completed), "", moment.isoformat()))
            if len(completed) != len(inventory["items"]):
                raise _ReadbackDeferred
            final = utc(now())
            earliest = min([utc(row[2]) for row in completed] + [utc(ledger.db.execute(
                "SELECT started_at FROM managed_snapshot_readback WHERE intent=?", (intent,)).fetchone()[0])])
            if not timedelta(0) <= final - earliest <= timedelta(hours=24):
                raise _ReadbackDeferred
            live = provider.read(snapshot["id"])
            if _identity(live) != _identity(snapshot) or not _marker(live, intent) or managed_catalog_reference(live) != journal_ref:
                raise ArchiveBlocked("Snapshot identity changed after complete readback")
            provider.require_idle_actions()
            check_binding()
            if science and hashlib.sha256(_private_read(os.environ.get("TIERED_ARCHIVE_BACKUP_KEY_FILE", ""), 33)).hexdigest() != key_sha:
                raise ArchiveBlocked("Archive encryption key changed during readback")
            return {"snapshot_id": snapshot["id"], "inventory_sha256": inventory_sha,
                    "objects": len(inventory["items"]) + ledger.db.execute(
                        "SELECT count(*) FROM managed_snapshot_readback_cache WHERE intent=?", (intent,)).fetchone()[0],
                    "bytes": sum(item["reference"]["bytes"] for item in inventory["items"]) + ledger.db.execute(
                        "SELECT coalesce(sum(bytes),0) FROM managed_snapshot_readback_cache WHERE intent=?", (intent,)).fetchone()[0],
                    "checked_at": final.isoformat(), "journal_reference": journal_ref}
    except _ReadbackDeferred:
        return None


def _completed_readback(ledger, intent, proof):
    header = ledger.db.execute(
        "SELECT inventory_sha256,started_at FROM managed_snapshot_readback WHERE intent=?", (intent,)
    ).fetchone()
    if not header or header[0] != proof["inventory_sha256"]:
        return None
    key_record = ledger.db.execute("SELECT key_sha256 FROM managed_snapshot_readback_key WHERE intent=?", (intent,)).fetchone()
    if key_record is None:
        return None
    if key_record[0] is not None:
        key_sha = os.getenv("TIERED_ARCHIVE_BACKUP_KEY_SHA256", "")
        if (key_sha != key_record[0] or not re.fullmatch(r"[a-f0-9]{64}", key_sha)
            or hashlib.sha256(_private_read(os.environ.get("TIERED_ARCHIVE_BACKUP_KEY_FILE", ""), 33)).hexdigest() != key_sha):
            raise ArchiveBlocked("Archive encryption key binding differs")
    items = ledger.db.execute(
        "SELECT position,item_sha256,verified_at FROM managed_snapshot_readback_item WHERE intent=? ORDER BY position", (intent,)
    ).fetchall()
    cache = ledger.db.execute(
        "SELECT count(*) FROM managed_snapshot_readback_cache WHERE intent=?", (intent,)
    ).fetchone()[0]
    checked = utc(proof["checked_at"])
    if (not items or [row[0] for row in items] != list(range(len(items)))
        or len(items) + cache != proof["objects"]
        or not timedelta(0) <= checked - utc(header[1]) <= timedelta(hours=24)
        or any(not utc(header[1]) <= utc(row[2]) <= checked for row in items)):
        return None
    return {row[1] for row in items}


def _rebind_bundle_from_readback(ledger, provider, store, survivor, receipt, now):
    """Use the matching complete snapshot inventory, preserving the first verification."""
    snapshot, proof = json.loads(survivor[3]), json.loads(survivor[4])
    hashes = _completed_readback(ledger, survivor[0], proof)
    candidate = candidate_from_json(receipt["source"])
    manifest = {"kind": "bundle-manifest", "reference": receipt["manifest"],
                "candidate": candidate_json(candidate), "identity": candidate.identity, "files": receipt["files"]}
    required = [manifest] + [{"kind": "bundle-file", "reference": ref} for ref in receipt["files"]]
    if (hashes is None or receipt["destination"] != store.identity
        or utc(snapshot["created"]) < utc(receipt["published_at"])
        or not timedelta(0) <= utc(now()) - utc(proof["checked_at"]) <= timedelta(hours=24)
        or any(hashlib.sha256(_json(item).encode()).hexdigest() not in hashes for item in required)):
        raise ArchiveBlocked("Fresh survivor inventory does not cover the exact bundle")
    live = provider.read(snapshot["id"])
    if (_identity(live) != snapshot or not _marker(live, survivor[0])
        or managed_catalog_reference(live) != proof["journal_reference"]):
        raise ArchiveBlocked("Managed survivor changed before bundle proof rebind")
    provider.require_idle_actions()
    checked = utc(proof["checked_at"])
    return ledger.save(candidate, "verified", dict(receipt, verified_at=checked.isoformat(), snapshot={
        "provider_id": snapshot["id"], "box_id": snapshot["storage_box"], "name": snapshot["name"],
        "created_at": utc(snapshot["created"]).isoformat(), "checked_at": checked.isoformat(),
    }), checked)


def _verify(provider, snapshot, reference, intent, ledger, store, checkpoint, now):
    """Read the frozen catalogue and every manifest and bundle body from this snapshot."""
    counts = {"objects": 0, "bytes": 0}
    digest = hashlib.sha256()

    class SnapshotStore:
        def download(self, key, path, size):
            checkpoint()
            store.download(key, path, size, snapshot=snapshot["name"])
            counts["objects"] += 1
            counts["bytes"] += size
            digest.update(_json([key, size]).encode())

    with tempfile.TemporaryDirectory(dir=ledger.root) as folder:
        journal = Ledger(Path(folder) / "journal-catalog")
        try:
            journal_ref = managed_catalog_reference(snapshot)
            if journal_ref["key"].split("/", 1)[0] != reference["key"].split("/", 1)[0]:
                raise ArchiveBlocked("Managed snapshot recovery environment differs")
            restore(
                journal_ref,
                journal,
                SnapshotStore(),
                checkpoint=checkpoint,
                expected_environment=journal_ref["key"].split("/", 1)[0],
            )
            row = journal.db.execute(
                "SELECT state,catalog FROM managed_snapshot WHERE intent=?", (intent,)
            ).fetchone()
            if not row or row[0] != "intent" or json.loads(row[1]) != reference:
                raise ArchiveBlocked("Snapshot ownership journal readback differs")
        finally:
            journal.close()
        copied = Ledger(Path(folder) / "catalog")
        try:
            restore(
                reference,
                copied,
                SnapshotStore(),
                checkpoint=checkpoint,
                expected_environment=reference["key"].split("/", 1)[0],
            )
            if not copied.db.execute("SELECT 1 FROM bundle LIMIT 1").fetchone():
                raise ArchiveBlocked("Empty snapshot catalogue cannot prove backup coverage")
            backup_sources = set()
            for (raw,) in copied.db.execute("SELECT receipt FROM bundle ORDER BY id"):
                receipt = json.loads(raw)
                candidate = candidate_from_json(receipt["source"])
                if receipt["destination"] != store.identity or utc(receipt["published_at"]) > utc(
                    snapshot["created"]
                ):
                    raise ArchiveBlocked("Snapshot catalogue destination or publication differs")
                if (
                    candidate.dataset == "database_backup"
                    and candidate.source in {"gateway", "direct"}
                    and utc(snapshot["created"]) - timedelta(hours=12)
                    <= candidate.end
                    <= utc(snapshot["created"])
                ):
                    backup_sources.add(candidate.source)
                for number, ref in enumerate([receipt["manifest"], *receipt["files"]]):
                    checkpoint()
                    path = Path(folder) / "object"
                    SnapshotStore().download(ref["key"], path, ref["bytes"])
                    verify_file(path, ref, checkpoint)
                    digest.update(ref["sha256"].encode())
                    if number == 0:
                        manifest = json.loads(path.read_text())
                        if (
                            manifest.get("identity") != candidate.identity
                            or manifest.get("candidate") != candidate_json(candidate)
                            or manifest.get("files") != receipt["files"]
                            or manifest.get("policy") != POLICY_SHA256
                        ):
                            raise ArchiveBlocked("Snapshot bundle manifest differs")
                    path.unlink()
            if reference["key"].startswith("production/") and backup_sources != {
                "gateway",
                "direct",
            }:
                raise ArchiveBlocked("Snapshot catalogue lacks both recent production backups")
        finally:
            copied.close()
    if _identity(provider.read(snapshot["id"])) != _identity(snapshot):
        raise ArchiveBlocked("Snapshot identity changed during complete readback")
    return {
        "snapshot_id": snapshot["id"],
        "inventory_sha256": digest.hexdigest(),
        **counts,
        "checked_at": now().isoformat(),
        "journal_reference": journal_ref,
    }


def cycle(
    ledger,
    store,
    provider,
    checkpoint,
    *,
    now=lambda: datetime.now(UTC),
    dependency_guard=None,
    enforce_scope=False,
    resumable_readback=None,
    readback_max_bytes=64 * 1024**2,
):
    """Run one cycle under the existing worker's exclusive lock and safety checkpoints."""
    if os.getenv("TIERED_ARCHIVE_ENVIRONMENT") == "production" and not enforce_scope:
        raise ArchiveBlocked("Production snapshot lifecycle requires managed scope")
    if resumable_readback is None:
        resumable_readback = enforce_scope
    if type(resumable_readback) is not bool or (enforce_scope and not resumable_readback):
        raise ArchiveBlocked("Production snapshot readback must be resumable")
    configured_budget = os.getenv("TIERED_ARCHIVE_SNAPSHOT_READBACK_MAX_BYTES")
    if configured_budget is not None:
        if not re.fullmatch(r"[0-9]{1,8}", configured_budget):
            raise ArchiveBlocked("Bounded snapshot readback byte budget required")
        readback_max_bytes = min(readback_max_bytes, int(configured_budget))
    if type(readback_max_bytes) is not int or not 1 <= readback_max_bytes <= 64 * 1024**2:
        raise ArchiveBlocked("Bounded snapshot readback byte budget required")
    db = ledger.db
    initialize_managed(db)
    initialize_snapshot_pins(db)
    if resumable_readback:
        initialize_snapshot_readback(db)
    db.execute("""CREATE TABLE IF NOT EXISTS managed_snapshot_action (
        intent TEXT NOT NULL, operation TEXT NOT NULL, action_id INTEGER NOT NULL,
        PRIMARY KEY(intent, operation))""")
    db.commit()
    scope = None
    if enforce_scope:
        from app.raw_archive.coordination import bound_job_lock

        bound_job_lock()
        scope = db.execute(
            "SELECT epoch,contract_sha256,recovery_state FROM managed_snapshot_scope WHERE id=1"
        ).fetchone()
        expected = os.environ.get("TIERED_ARCHIVE_SNAPSHOT_DEPENDENCIES_SHA256", "")
        config_path = os.environ.get("TIERED_ARCHIVE_SNAPSHOT_DEPENDENCIES_FILE", "")
        if (
            not scope
            or scope[2] != "current"
            or os.getenv("RAW_ARCHIVE_MANAGED_SCOPE_REQUIRED") != "true"
            or not re.fullmatch(r"[a-f0-9]{64}", expected)
            or scope[1] != expected
        ):
            raise ArchiveBlocked("Current pinned managed snapshot scope required")
        raw = _private_read(config_path, 1024 * 1024)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ArchiveBlocked("Managed snapshot scope contract differs")
        try:
            contract = json.loads(raw)
        except ValueError:
            raise ArchiveBlocked("Managed snapshot scope contract invalid") from None
        if (
            type(contract) is not dict
            or contract.get("schema") not in {2, 3}
            or contract.get("environment") != "production"
            or contract.get("epoch") != scope[0]
            or contract.get("box_id") != provider.box_id
        ):
            raise ArchiveBlocked("Managed snapshot scope identity differs")
        validate_local_scope_bindings(contract, ledger, provider)

    def confirm_action(intent, snapshot_id, operation):
        row = db.execute(
            "SELECT action_id FROM managed_snapshot_action WHERE intent=? AND operation=?",
            (intent, operation),
        ).fetchone()
        action_id = provider.require_action(row[0] if row else None, snapshot_id, operation)
        if not row:
            if type(action_id) is not int or action_id <= 0:
                raise ArchiveBlocked("Reconciled provider action identity is unknown")
            with db:
                db.execute(
                    "INSERT INTO managed_snapshot_action VALUES(?,?,?)",
                    (intent, operation, action_id),
                )

    def remember_action(intent, operation, response):
        action = response.get("action") if type(response) is dict else None
        action_id = action.get("id") if type(action) is dict else None
        if type(action_id) is not int or action_id <= 0:
            raise ArchiveBlocked("Provider snapshot action identity is unknown")
        with db:
            db.execute(
                "INSERT INTO managed_snapshot_action VALUES(?,?,?)", (intent, operation, action_id)
            )

    checkpoint()
    snapshots, capacity = provider.inventory()
    rows = db.execute(
        "SELECT intent,state,catalog,snapshot,proof,due FROM managed_snapshot ORDER BY rowid"
    ).fetchall()
    if any(
        row[1] not in {"intent", "bound", "verified", "delete_intent", "deleted"} for row in rows
    ):
        raise ArchiveBlocked("Unknown managed snapshot journal state")
    active = [row for row in rows if row[1] != "deleted"]
    if scope:
        for row in active:
            binding = db.execute(
                "SELECT epoch FROM managed_snapshot_epoch WHERE intent=?", (row[0],)
            ).fetchone()
            if not binding or binding[0] != scope[0]:
                raise ArchiveBlocked("Existing snapshot predates managed scope")
    if sum(row[1] in {"intent", "bound", "delete_intent"} for row in active) > 1:
        raise ArchiveBlocked("Multiple unresolved managed snapshot intents")

    # A prior DELETE is never repeated after an unknown outcome.
    deleting = next((row for row in active if row[1] == "delete_intent"), None)
    if deleting:
        old = json.loads(deleting[3])
        confirm_action(deleting[0], old["id"], "delete")
        if any(s["id"] == old["id"] for s in snapshots):
            raise ArchiveBlocked("Snapshot deletion outcome requires provider reconciliation")
        survivors = [row for row in active if row[1] == "verified"]
        if len(survivors) != 2:
            raise ArchiveBlocked("Two verified managed survivors required")
        for row in survivors:
            expected = json.loads(row[3])
            live = next((s for s in snapshots if s["id"] == expected["id"]), None)
            if (
                not live
                or _identity(live) != expected
                or not _marker(live, row[0])
                or managed_catalog_reference(live) != json.loads(row[4]).get("journal_reference")
            ):
                raise ArchiveBlocked("Managed survivor identity differs")
        managed_snapshot_plan(
            snapshots,
            [{"snapshot": json.loads(row[3]), "proof": json.loads(row[4])} for row in survivors],
            box_id=provider.box_id,
            capacity=capacity,
            now=now(),
        )
        with db:
            db.execute("UPDATE managed_snapshot SET state='deleted' WHERE intent=?", (deleting[0],))
        active.remove(deleting)

    pending = next((row for row in active if row[1] in {"intent", "bound"}), None)
    if pending and pending[1] == "intent":
        matches = [s for s in snapshots if _marker(s, pending[0])]
        if len(matches) != 1:
            raise ArchiveBlocked("Snapshot creation outcome requires provider reconciliation")
        bound = _identity(matches[0])
        with db:
            db.execute(
                "UPDATE managed_snapshot SET state='bound',snapshot=? WHERE intent=?",
                (_json(bound), pending[0]),
            )
        pending = (pending[0], "bound", pending[2], _json(bound), None, None)
    verified = [row for row in active if row[1] == "verified"]
    records = [{"snapshot": json.loads(row[3]), "proof": json.loads(row[4])} for row in verified]

    def rotate_if_needed():
        nonlocal snapshots, capacity, verified, records
        for row in verified:
            expected = json.loads(row[3])
            live = next((s for s in snapshots if s["id"] == expected["id"]), None)
            if (
                not live
                or _identity(live) != expected
                or not _marker(live, row[0])
                or managed_catalog_reference(live) != json.loads(row[4]).get("journal_reference")
            ):
                raise ArchiveBlocked("Managed snapshot ownership or provider identity differs")
        decision = managed_snapshot_plan(
            snapshots, records, box_id=provider.box_id, capacity=capacity, now=now()
        )
        if decision["action"] != "rotate":
            return
        victim = next(
            row for row in verified if json.loads(row[3])["id"] == decision["snapshot_id"]
        )
        survivors = [row for row in verified if row != victim]
        if resumable_readback:
            for survivor in survivors:
                old_proof = json.loads(survivor[4])
                age = utc(now()) - utc(old_proof["checked_at"])
                if age < timedelta(0):
                    raise ArchiveBlocked("Managed survivor proof is from the future")
                if age > timedelta(hours=24) or _completed_readback(ledger, survivor[0], old_proof) is None:
                    identity = json.loads(survivor[3])
                    live = next(s for s in snapshots if s["id"] == identity["id"])
                    proof = _verify_resumable(
                        provider, live, json.loads(survivor[2]), survivor[0], ledger,
                        store, checkpoint, now, max_bytes=readback_max_bytes, expected_state="verified",
                    )
                    if proof is not None:
                        with db:
                            db.execute("UPDATE managed_snapshot SET proof=? WHERE intent=? AND state='verified' AND proof=?",
                                       (_json(proof), survivor[0], survivor[4]))
                            if db.execute("SELECT changes()").fetchone()[0] != 1:
                                raise ArchiveBlocked("Managed survivor proof changed during refresh")
                    return SnapshotReadbackPending(status="READBACK_PENDING", snapshot_id=identity["id"])
        newest = max((json.loads(row[3]) for row in survivors), key=lambda s: (utc(s["created"]), s["id"]))
        # Refresh only live, completed catalogue proofs using the existing proof
        # contract; Ledger.save preserves the first verification/reserve date.
        # In-flight and evicted states remain conservative blockers below.
        last = ""
        while True:
            row = db.execute(
                "SELECT id,receipt FROM bundle WHERE id>? ORDER BY id LIMIT 1", (last,)
            ).fetchone()
            if not row:
                break
            last, raw = row
            receipt = json.loads(raw)
            if (
                receipt.get("snapshot", {}).get("provider_id") == decision["snapshot_id"]
                and receipt["state"] == "verified"
            ):
                if resumable_readback:
                    target_row = next(row for row in survivors if json.loads(row[3])["id"] == newest["id"])
                    _rebind_bundle_from_readback(ledger, provider, store, target_row, receipt, now)
                else:
                    verify_snapshot(
                        candidate_from_json(receipt["source"]), store, ledger, provider, newest["id"],
                        checkpoint=checkpoint, now=now(),
                    )
                receipt = ledger.get(last)
            if receipt.get("snapshot", {}).get("provider_id") == decision["snapshot_id"]:
                raise ArchiveBlocked("Snapshot still has active catalogue proof dependencies")
        if dependency_guard is None:
            raise ArchiveBlocked("External snapshot restore and deletion dependencies are unknown")
        dependency_guard(json.loads(victim[3]), [json.loads(row[3]) for row in verified])
        provider.require_idle_actions()
        if resumable_readback and any(
            not timedelta(0) <= utc(now()) - utc(json.loads(row[4])["checked_at"]) <= timedelta(hours=24)
            for row in survivors
        ):
            return SnapshotReadbackPending(status="READBACK_PENDING", snapshot_id=json.loads(victim[3])["id"])
        with db:
            db.execute(
                "UPDATE managed_snapshot SET state='delete_intent' WHERE intent=?", (victim[0],)
            )
        checkpoint()
        provider.require_idle_actions()
        target = provider.read(decision["snapshot_id"])
        if (
            _identity(target) != json.loads(victim[3])
            or not _marker(target, victim[0])
            or managed_catalog_reference(target) != json.loads(victim[4]).get("journal_reference")
        ):
            raise ArchiveBlocked("Managed snapshot changed before deletion")
        response = provider.delete_managed(decision["snapshot_id"], ledger=ledger, intent=victim[0])
        remember_action(victim[0], "delete", response)
        confirm_action(victim[0], decision["snapshot_id"], "delete")
        snapshots, capacity = provider.inventory()
        if any(s["id"] == decision["snapshot_id"] for s in snapshots):
            raise ArchiveBlocked("Snapshot deletion awaits provider confirmation")
        survivors = [row for row in verified if row != victim]
        for row in survivors:
            expected = json.loads(row[3])
            live = next((s for s in snapshots if s["id"] == expected["id"]), None)
            if (
                not live
                or _identity(live) != expected
                or not _marker(live, row[0])
                or managed_catalog_reference(live) != json.loads(row[4]).get("journal_reference")
            ):
                raise ArchiveBlocked("Managed survivor identity differs after deletion")
        survivor_records = [
            {"snapshot": json.loads(row[3]), "proof": json.loads(row[4])} for row in survivors
        ]
        managed_snapshot_plan(
            snapshots, survivor_records, box_id=provider.box_id, capacity=capacity, now=now()
        )
        with db:
            db.execute("UPDATE managed_snapshot SET state='deleted' WHERE intent=?", (victim[0],))
        verified, records = survivors, survivor_records

    if not pending:
        rotation = rotate_if_needed()
        if isinstance(rotation, SnapshotReadbackPending):
            return rotation
        if verified and max(utc(row[5]) for row in verified) > now():
            return max((json.loads(row[3]) for row in verified), key=lambda s: utc(s["created"]))[
                "id"
            ]
        if len(verified) >= 3:
            raise ArchiveBlocked("Snapshot rotation must complete before another creation")
        reference = backup(ledger, store, checkpoint=checkpoint)
        if reference.get("status") == "empty":
            raise ArchiveBlocked("Complete backup catalogue required before snapshot")
        intent = uuid.uuid4().hex
        with db:
            db.execute(
                "INSERT INTO managed_snapshot VALUES(?,?,?,NULL,NULL,NULL)",
                (intent, "intent", _json(reference)),
            )
            if scope:
                db.execute("INSERT INTO managed_snapshot_epoch VALUES(?,?)", (intent, scope[0]))
        # Publish a second immutable catalogue containing the durable intent.
        # Its reference is bound to provider labels for isolated recovery.
        journal_reference = backup(ledger, store, checkpoint=checkpoint)
        if journal_reference.get("status") == "empty":
            raise ArchiveBlocked("Managed snapshot ownership catalogue is empty")
        checkpoint()
        # On any error the durable intent remains; a future run only reconciles.
        response = provider.create_managed(intent, journal_reference)
        remember_action(intent, "create", response)
        snapshots, capacity = provider.inventory()
        matches = [s for s in snapshots if _marker(s, intent)]
        if len(matches) != 1:
            raise ArchiveBlocked("Snapshot creation requires provider confirmation")
        bound = _identity(matches[0])
        with db:
            db.execute(
                "UPDATE managed_snapshot SET state='bound',snapshot=? WHERE intent=?",
                (_json(bound), intent),
            )
        pending = (intent, "bound", _json(reference), _json(bound), None, None)

    snapshot = json.loads(pending[3])
    live = next((s for s in snapshots if s["id"] == snapshot["id"]), None)
    if not live or _identity(live) != snapshot or not _marker(live, pending[0]):
        raise ArchiveBlocked("Managed snapshot ownership or provider identity differs")
    confirm_action(pending[0], snapshot["id"], "create")
    if resumable_readback:
        proof = _verify_resumable(provider, live, json.loads(pending[2]), pending[0], ledger,
                                  store, checkpoint, now, max_bytes=readback_max_bytes)
        if proof is None:
            return SnapshotReadbackPending(status="READBACK_PENDING", snapshot_id=snapshot["id"])
    else:
        proof = _verify(provider, live, json.loads(pending[2]), pending[0], ledger, store, checkpoint, now)
    confirm_action(pending[0], snapshot["id"], "create")
    current = provider.read(snapshot["id"])
    if (
        not _marker(current, pending[0])
        or managed_catalog_reference(current) != proof["journal_reference"]
    ):
        raise ArchiveBlocked("Managed snapshot ownership changed during readback")
    with db:
        db.execute(
            "UPDATE managed_snapshot SET state='verified',proof=?,due=? WHERE intent=?",
            (
                _json(proof),
                (utc(snapshot["created"]) + timedelta(hours=12)).isoformat(),
                pending[0],
            ),
        )
    verified.append(
        (
            pending[0],
            "verified",
            pending[2],
            _json(snapshot),
            _json(proof),
            (utc(snapshot["created"]) + timedelta(hours=12)).isoformat(),
        )
    )
    records = [{"snapshot": json.loads(row[3]), "proof": json.loads(row[4])} for row in verified]
    snapshots, capacity = provider.inventory()
    # The just-created ID was not present in the incoming root handoffs.
    # A subsequent invocation supplies fresh rosters before any rotation.
    if not enforce_scope and not resumable_readback:
        rotation = rotate_if_needed()
        if isinstance(rotation, SnapshotReadbackPending):
            return rotation
    return snapshot["id"]
