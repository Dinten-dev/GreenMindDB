"""Journalled snapshots of the existing tiered archive catalogue and its bundles."""

import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.snapshot_api import managed_catalog_reference, managed_snapshot_plan

from .ledger import Ledger
from .pipeline import candidate_json, verify_file, verify_snapshot
from .policy import POLICY_SHA256, utc
from .registry_backup import backup, initialize_managed, restore
from .runtime import candidate_from_json


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


def check_external_dependencies(victim, _verified):
    """Inspect pinned raw journals; untracked restore sessions still force HOLD."""
    path = Path(os.environ.get("TIERED_ARCHIVE_SNAPSHOT_DEPENDENCIES_FILE", ""))
    pin = os.environ.get("TIERED_ARCHIVE_SNAPSHOT_DEPENDENCIES_SHA256", "")
    if not path.is_absolute() or path.is_symlink() or not re.fullmatch(r"[a-f0-9]{64}", pin):
        raise ArchiveBlocked("Pinned external snapshot dependency catalogue required")
    try:
        with path.open("rb") as body:
            info = os.fstat(body.fileno())
            raw = body.read(1024 * 1024 + 1)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid not in {0, os.getuid()}
            or info.st_mode & 0o077
            or len(raw) > 1024 * 1024
            or hashlib.sha256(raw).hexdigest() != pin
        ):
            raise ArchiveBlocked("External snapshot dependency catalogue differs")
        config = json.loads(raw)
    except (OSError, ValueError):
        raise ArchiveBlocked("External snapshot dependency catalogue unavailable") from None
    if (
        type(config) is not dict
        or set(config) != {"schema", "environment", "raw_ledgers"}
        or config["schema"] != 1
        or config["environment"] != "production"
        or type(config["raw_ledgers"]) is not list
        or not 1 <= len(config["raw_ledgers"]) <= 16
    ):
        raise ArchiveBlocked("External raw archive journal bindings are unknown")
    seen = set()
    for item in config["raw_ledgers"]:
        if (
            type(item) is not dict
            or set(item) != {"path", "device", "inode"}
            or type(item["path"]) is not str
            or type(item["device"]) is not int
            or type(item["inode"]) is not int
        ):
            raise ArchiveBlocked("Pinned raw archive journal identity required")
        journal = Path(item["path"])
        if not journal.is_absolute() or journal.is_symlink() or str(journal) in seen:
            raise ArchiveBlocked("Raw archive journal path is unknown")
        seen.add(str(journal))
        try:
            metadata = journal.stat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_dev != item["device"]
                or metadata.st_ino != item["inode"]
            ):
                raise ArchiveBlocked("Raw archive journal identity differs")
            db = sqlite3.connect(journal.as_uri() + "?mode=ro", uri=True, timeout=2)
            try:
                for state, raw_receipt in db.execute("SELECT state,receipt FROM archive"):
                    if state not in {"verified", "deleting", "evicted"}:
                        raise ArchiveBlocked("Unknown raw archive journal state")
                    if state == "deleting":
                        raise ArchiveBlocked("Active raw archive deletion intent")
                    receipt = json.loads(raw_receipt)
                    if type(receipt) is not dict:
                        raise ArchiveBlocked("Unknown raw archive receipt")
                    snapshot = receipt.get("provider_snapshot")
                    if snapshot is not None and (
                        type(snapshot) is not dict
                        or snapshot.get("provider_id") == victim["id"]
                        or snapshot.get("name") == victim["name"]
                    ):
                        raise ArchiveBlocked("Raw archive snapshot dependency is active or unknown")
            finally:
                db.close()
        except (OSError, sqlite3.Error, ValueError, TypeError):
            raise ArchiveBlocked("Raw archive journal cannot be inspected") from None
    # Restore sessions are journalled in separate PostgreSQL targets, while
    # pilot manifests are separate operator files. No exhaustive pinned
    # inventory or common lease exists yet; no SQLite scan can prove absence.
    raise ArchiveBlocked("External restore and pilot dependency coverage is unknown")


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
    ledger, store, provider, checkpoint, *, now=lambda: datetime.now(UTC), dependency_guard=None
):
    """Run one cycle under the existing worker's exclusive lock and safety checkpoints."""
    db = ledger.db
    initialize_managed(db)
    db.execute("""CREATE TABLE IF NOT EXISTS managed_snapshot_action (
        intent TEXT NOT NULL, operation TEXT NOT NULL, action_id INTEGER NOT NULL,
        PRIMARY KEY(intent, operation))""")
    db.commit()

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
        newest = max((json.loads(row[3]) for row in verified), key=lambda s: utc(s["created"]))
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
                verify_snapshot(
                    candidate_from_json(receipt["source"]),
                    store,
                    ledger,
                    provider,
                    newest["id"],
                    checkpoint=checkpoint,
                    now=now(),
                )
                receipt = ledger.get(last)
            if receipt.get("snapshot", {}).get("provider_id") == decision["snapshot_id"]:
                raise ArchiveBlocked("Snapshot still has active catalogue proof dependencies")
        if dependency_guard is None:
            raise ArchiveBlocked("External snapshot restore and deletion dependencies are unknown")
        dependency_guard(json.loads(victim[3]), [json.loads(row[3]) for row in verified])
        with db:
            db.execute(
                "UPDATE managed_snapshot SET state='delete_intent' WHERE intent=?", (victim[0],)
            )
        checkpoint()
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
        rotate_if_needed()
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
    proof = _verify(
        provider, live, json.loads(pending[2]), pending[0], ledger, store, checkpoint, now
    )
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
    rotate_if_needed()
    return snapshot["id"]
