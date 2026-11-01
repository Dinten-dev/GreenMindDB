"""Durable per-revision proofs, intents and cache-independent archive index."""

import hashlib
import json
import os
import re
import sqlite3
import stat
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.raw_archive.policy import ArchiveBlocked

from .policy import POLICY_SHA256


def initialize_snapshot_pins(db):
    db.execute("""CREATE TABLE IF NOT EXISTS managed_snapshot_pin (
      id TEXT PRIMARY KEY, intent TEXT NOT NULL, snapshot_id INTEGER NOT NULL,
      kind TEXT NOT NULL, operation_sha256 TEXT NOT NULL, owner TEXT NOT NULL,
      state TEXT NOT NULL, acquired_at TEXT NOT NULL, completed_at TEXT,
      completion_sha256 TEXT)""")
    db.execute("""CREATE TABLE IF NOT EXISTS managed_snapshot_pin_event (
      ordinal INTEGER PRIMARY KEY AUTOINCREMENT, pin_id TEXT NOT NULL,
      state TEXT NOT NULL, at TEXT NOT NULL, evidence TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS managed_snapshot_scope (
      id INTEGER PRIMARY KEY CHECK(id=1), epoch TEXT NOT NULL, contract_sha256 TEXT NOT NULL,
      recovery_state TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS managed_snapshot_epoch (
      intent TEXT PRIMARY KEY, epoch TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS managed_snapshot_pin_receipt (
      pin_id TEXT PRIMARY KEY, receipt TEXT NOT NULL, receipt_sha256 TEXT NOT NULL)""")


def initialize_snapshot_readback(db):
    """Local resumable evidence; loss of this cache causes safe re-verification."""
    db.execute("""CREATE TABLE IF NOT EXISTS managed_snapshot_readback (
      intent TEXT PRIMARY KEY, binding_sha256 TEXT NOT NULL, inventory_sha256 TEXT,
      started_at TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS managed_snapshot_readback_cache (
      intent TEXT NOT NULL, object_key TEXT NOT NULL, bytes INTEGER NOT NULL,
      sha256 TEXT NOT NULL, verified_at TEXT NOT NULL,
      PRIMARY KEY(intent,object_key))""")
    db.execute("""CREATE TABLE IF NOT EXISTS managed_snapshot_readback_item (
      intent TEXT NOT NULL, position INTEGER NOT NULL, item_sha256 TEXT NOT NULL,
      verified_at TEXT NOT NULL, PRIMARY KEY(intent,position))""")
    db.execute("""CREATE TABLE IF NOT EXISTS managed_snapshot_readback_key (
      intent TEXT PRIMARY KEY, key_sha256 TEXT)""")
    db.execute("""CREATE TABLE IF NOT EXISTS managed_snapshot_cache_owner (
      intent TEXT PRIMARY KEY, binding_sha256 TEXT NOT NULL, state TEXT NOT NULL,
      device INTEGER, inode INTEGER, cleanup_manifest TEXT)""")
    db.execute("""CREATE TABLE IF NOT EXISTS managed_snapshot_cache_file (
      intent TEXT NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL,
      PRIMARY KEY(intent,sha256))""")


def _sha(value):
    return type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _private_read(path, maximum):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink() or path.resolve(strict=False) != path:
        raise ArchiveBlocked("Private operation journal path required")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as body:
            info = os.fstat(body.fileno())
            raw = body.read(maximum + 1)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid not in {0, os.getuid()}
                or info.st_mode & 0o077
                or len(raw) > maximum
            ):
                raise ArchiveBlocked("Private bounded operation journal required")
            after = os.fstat(body.fileno())
            current = path.stat(follow_symlinks=False)
            if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
                info.st_dev,
                info.st_ino,
                info.st_size,
                info.st_mtime_ns,
            ) or (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise ArchiveBlocked("Operation journal changed during readback")
            return raw
    except OSError:
        raise ArchiveBlocked("Operation journal unavailable") from None


def _private_file_digest(path, size):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink() or path.resolve(strict=False) != path:
        raise ArchiveBlocked("Private frozen artifact path required")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as body:
            info = os.fstat(body.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid not in {0, os.getuid()}
                or info.st_mode & 0o077
                or info.st_mode & 0o222
                or info.st_size != size
            ):
                raise ArchiveBlocked("Private frozen artifact identity differs")
            digest = hashlib.sha256()
            while chunk := body.read(64 * 1024):
                digest.update(chunk)
            after = os.fstat(body.fileno())
            current = path.stat(follow_symlinks=False)
            if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
                info.st_dev,
                info.st_ino,
                info.st_size,
                info.st_mtime_ns,
            ) or (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise ArchiveBlocked("Frozen artifact changed during readback")
            return digest.hexdigest()
    except OSError:
        raise ArchiveBlocked("Frozen artifact unavailable") from None


class Ledger:
    def __init__(self, root, *, read_only=False):
        root = Path(root)
        if not root.is_absolute() or root.is_symlink():
            raise ArchiveBlocked("Isolated absolute archive state required")
        self.read_only = read_only
        if read_only:
            self.root = root
            self.db = sqlite3.connect(
                (root / "catalog-read.sqlite3").as_uri() + "?mode=ro&immutable=1",
                uri=True,
                timeout=3,
            )
            self.db.execute("PRAGMA query_only=ON")
            return
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root = root
        self.db = sqlite3.connect(root / "tiered.sqlite3", timeout=3)
        os.chmod(root / "tiered.sqlite3", 0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS bundle (
            id TEXT PRIMARY KEY, environment TEXT NOT NULL, source TEXT NOT NULL,
            organization TEXT NOT NULL, dataset TEXT NOT NULL, start TEXT NOT NULL,
            end TEXT NOT NULL, receipt TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS bundle_scope ON bundle(environment,source,organization,dataset,start,end);
          CREATE TABLE IF NOT EXISTS event (
            ordinal INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
            candidate TEXT NOT NULL, state TEXT NOT NULL, body TEXT NOT NULL);
        """)

    def close(self):
        self.db.close()

    def acquire_snapshot_pin(
        self,
        *,
        pin_id,
        snapshot_id,
        kind,
        operation_path,
        operation_sha256,
        owner,
        provider,
        now=None,
    ):
        from app.raw_archive.coordination import bound_job_lock

        from .managed_snapshot import _identity, _marker

        bound_job_lock()
        if self.read_only:
            raise ArchiveBlocked("Read API cannot acquire snapshot pins")
        initialize_snapshot_pins(self.db)
        scope = self.db.execute(
            "SELECT epoch,recovery_state FROM managed_snapshot_scope WHERE id=1"
        ).fetchone()
        if not scope or scope[1] != "current":
            raise ArchiveBlocked("Current managed snapshot scope required")
        if (
            type(pin_id) is not str
            or not re.fullmatch(r"[a-f0-9]{32}", pin_id)
            or type(snapshot_id) is not int
            or snapshot_id <= 0
            or kind not in {"restoreinput", "raw_pilot", "provider_reader", "root_check"}
            or not _sha(operation_sha256)
            or type(owner) is not dict
            or set(owner) != {"host", "unit", "invocation", "pid_generation"}
            or any(type(value) is not str or not 1 <= len(value) <= 256 for value in owner.values())
        ):
            raise ArchiveBlocked("Exact snapshot operation and owner identity required")
        raw = _private_read(operation_path, 1024 * 1024)
        if hashlib.sha256(raw).hexdigest() != operation_sha256:
            raise ArchiveBlocked("Snapshot operation journal digest differs")
        try:
            operation = json.loads(raw)
        except ValueError:
            raise ArchiveBlocked("Snapshot operation journal is invalid") from None
        if (
            type(operation) is not dict
            or operation.get("pin_id") != pin_id
            or operation.get("snapshot_id") != snapshot_id
            or operation.get("kind") != kind
            or operation.get("owner") != owner
            or operation.get("scope_epoch") != scope[0]
            or not _sha(operation.get("input_sha256"))
            or (
                kind == "restoreinput"
                and (
                    not _sha(operation.get("target_sha256"))
                    or not _sha(operation.get("restore_plan_sha256"))
                    or type(operation.get("input_manifest_path")) is not str
                )
            )
        ):
            raise ArchiveBlocked("Snapshot operation journal binding differs")
        allowed_entries = {
            "raw_pilot": {"raw-pilot"},
            "restoreinput": {"raw-restore", "wav-catalog"},
            "provider_reader": {"snapshot-readback"},
            "root_check": {"snapshot-readback"},
        }
        if operation.get("consumer_entrypoint") not in allowed_entries[kind]:
            raise ArchiveBlocked("Reviewed snapshot consumer entrypoint required")
        if kind == "restoreinput":
            source_manifest = _private_read(operation["input_manifest_path"], 1024 * 1024)
            if hashlib.sha256(source_manifest).hexdigest() != operation["input_sha256"]:
                raise ArchiveBlocked("Pinned restore input manifest differs")
        rows = self.db.execute(
            "SELECT intent,snapshot FROM managed_snapshot WHERE state='verified'"
        ).fetchall()
        matches = [
            (intent, json.loads(raw))
            for intent, raw in rows
            if json.loads(raw).get("id") == snapshot_id
        ]
        if len(matches) != 1:
            raise ArchiveBlocked("Verified managed snapshot required before pin")
        intent, saved = matches[0]
        binding = self.db.execute(
            "SELECT epoch FROM managed_snapshot_epoch WHERE intent=?", (intent,)
        ).fetchone()
        if not binding or binding[0] != scope[0]:
            raise ArchiveBlocked("Snapshot predates current managed scope")
        live = provider.read(snapshot_id)
        if _identity(live) != saved or not _marker(live, intent):
            raise ArchiveBlocked("Managed snapshot provider identity changed before pin")
        encoded_owner = json.dumps(owner, sort_keys=True, separators=(",", ":"))
        previous = self.db.execute(
            "SELECT intent,snapshot_id,kind,operation_sha256,owner,state FROM managed_snapshot_pin WHERE id=?",
            (pin_id,),
        ).fetchone()
        if previous:
            if previous != (intent, snapshot_id, kind, operation_sha256, encoded_owner, "active"):
                raise ArchiveBlocked(
                    "Existing snapshot pin belongs to another or unresolved operation"
                )
            # Recovery of the same live process never creates a second pin.
            return pin_id
        at = (now or datetime.now(UTC)).isoformat()
        with self.db:
            self.db.execute(
                "INSERT INTO managed_snapshot_pin VALUES(?,?,?,?,?,?,'active',?,NULL,NULL)",
                (pin_id, intent, snapshot_id, kind, operation_sha256, encoded_owner, at),
            )
            self.db.execute(
                "INSERT INTO managed_snapshot_pin_event(pin_id,state,at,evidence) VALUES(?,?,?,?)",
                (pin_id, "active", at, operation_sha256),
            )
        return pin_id

    def release_completed_pilot_pin(
        self, *, pin_id, operation_path, operation_sha256, owner, replay_guard, now=None
    ):
        """Release one completed pilot only after its exact owned-wait proof."""
        from app.raw_archive.coordination import bound_job_lock

        bound_job_lock()
        if not _sha(operation_sha256) or replay_guard is None:
            raise ArchiveBlocked("Pinned pilot operation and owned-wait proof required")
        row = self.db.execute(
            "SELECT snapshot_id,kind,operation_sha256,owner,state FROM managed_snapshot_pin WHERE id=?",
            (pin_id,),
        ).fetchone()
        if (not row or row[1] != "raw_pilot" or row[2] != operation_sha256
            or json.loads(row[3]) != owner or row[4] not in {"active", "uncertain"}):
            raise ArchiveBlocked("Exact pending pilot pin required")
        raw = _private_read(operation_path, 1024 * 1024)
        if hashlib.sha256(raw).hexdigest() != operation_sha256:
            raise ArchiveBlocked("Snapshot operation journal digest differs")
        try:
            operation = json.loads(raw)
        except ValueError:
            raise ArchiveBlocked("Snapshot operation journal is invalid") from None
        if (type(operation) is not dict or operation.get("pin_id") != pin_id
            or operation.get("snapshot_id") != row[0] or operation.get("kind") != "raw_pilot"
            or operation.get("consumer_entrypoint") != "raw-pilot" or operation.get("owner") != owner):
            raise ArchiveBlocked("Pilot operation journal binding differs")
        evidence = replay_guard(pin_id, operation, None)
        if not _sha(evidence):
            raise ArchiveBlocked("Owned-wait proof digest required")
        at = (now or datetime.now(UTC)).isoformat()
        with self.db:
            updated = self.db.execute(
                "UPDATE managed_snapshot_pin SET state='released',completed_at=?,completion_sha256=? "
                "WHERE id=? AND state IN ('active','uncertain')",
                (at, evidence, pin_id),
            )
            if updated.rowcount != 1:
                raise ArchiveBlocked("Pilot pin changed before release")
            self.db.execute(
                "INSERT INTO managed_snapshot_pin_event(pin_id,state,at,evidence) VALUES(?,?,?,?)",
                (pin_id, "released", at, evidence),
            )

    def release_frozen_snapshot_pin(
        self,
        *,
        pin_id,
        operation_path,
        operation_sha256,
        owner,
        frozen_manifest,
        manifest_sha256,
        now=None,
        replay_guard=None,
    ):
        from app.raw_archive.coordination import bound_job_lock

        from .runtime import runtime_sha256

        bound_job_lock()
        if not _sha(manifest_sha256) or not _sha(operation_sha256):
            raise ArchiveBlocked("Pinned frozen-input proof required")
        row = self.db.execute(
            "SELECT snapshot_id,kind,operation_sha256,owner,state FROM managed_snapshot_pin WHERE id=?",
            (pin_id,),
        ).fetchone()
        if (
            not row
            or row[1] != "restoreinput"
            or row[2] != operation_sha256
            or json.loads(row[3]) != owner
            or row[4] not in {"active", "uncertain"}
        ):
            raise ArchiveBlocked("Exact pending restore pin required")
        operation_raw = _private_read(operation_path, 1024 * 1024)
        if hashlib.sha256(operation_raw).hexdigest() != operation_sha256:
            raise ArchiveBlocked("Snapshot operation journal digest differs")
        try:
            operation = json.loads(operation_raw)
            input_raw = _private_read(operation["input_manifest_path"], 1024 * 1024)
            if hashlib.sha256(input_raw).hexdigest() != operation["input_sha256"]:
                raise ValueError
            input_manifest = json.loads(input_raw)
        except (TypeError, ValueError, KeyError):
            raise ArchiveBlocked("Pinned restore input manifest is invalid") from None
        if (
            type(input_manifest) is not dict
            or set(input_manifest) != {"schema", "files"}
            or input_manifest["schema"] != 1
            or type(input_manifest["files"]) is not list
            or not 1 <= len(input_manifest["files"]) <= 4096
        ):
            raise ArchiveBlocked("Complete restore input inventory required")
        raw = _private_read(frozen_manifest, 1024 * 1024)
        if hashlib.sha256(raw).hexdigest() != manifest_sha256:
            raise ArchiveBlocked("Frozen restore manifest digest differs")
        try:
            manifest = json.loads(raw)
        except ValueError:
            raise ArchiveBlocked("Frozen restore manifest is invalid") from None
        if (
            type(manifest) is not dict
            or set(manifest)
            != {
                "schema",
                "pin_id",
                "snapshot_id",
                "operation_sha256",
                "local_only",
                "files",
                "target_sha256",
                "restore_plan_sha256",
                "local_runtime_sha256",
                "local_executor",
            }
            or manifest["schema"] != 1
            or manifest["pin_id"] != pin_id
            or manifest["snapshot_id"] != row[0]
            or manifest["operation_sha256"] != operation_sha256
            or manifest["local_only"] is not True
            or manifest["target_sha256"] != operation["target_sha256"]
            or manifest["restore_plan_sha256"] != operation["restore_plan_sha256"]
            or manifest["local_runtime_sha256"] != runtime_sha256()
            or not (
                manifest["local_executor"] == "app.tiered_archive.database_restore.restore_backup"
                or manifest["local_executor"] == {
                    "raw-restore": "app.raw_archive.recovery.restore_bundle",
                    "wav-catalog": "app.raw_archive.wav_catalog.restore_bundle",
                }.get(operation.get("consumer_entrypoint"), "")
            )
            or type(manifest["files"]) is not list
            or not 1 <= len(manifest["files"]) <= 4096
        ):
            raise ArchiveBlocked("Complete frozen restore input evidence required")
        expected_files = input_manifest["files"]
        if any(
            type(item) is not dict
            or set(item) != {"sha256", "bytes"}
            or not _sha(item["sha256"])
            or type(item["bytes"]) is not int
            or not 0 < item["bytes"] <= 64 * 1024**2
            for item in expected_files
        ) or len(expected_files) != len(manifest["files"]):
            raise ArchiveBlocked("Pinned restore input inventory differs")
        for item in manifest["files"]:
            if (
                type(item) is not dict
                or set(item) != {"path", "sha256", "bytes"}
                or not _sha(item["sha256"])
                or type(item["bytes"]) is not int
                or not 0 < item["bytes"] <= 64 * 1024**2
            ):
                raise ArchiveBlocked("Invalid frozen restore artifact")
            if _private_file_digest(item["path"], item["bytes"]) != item["sha256"]:
                raise ArchiveBlocked("Frozen restore artifact differs")
        if sorted((item["sha256"], item["bytes"]) for item in expected_files) != sorted(
            (item["sha256"], item["bytes"]) for item in manifest["files"]
        ):
            raise ArchiveBlocked("Frozen restore artifacts do not cover the pinned input inventory")
        if replay_guard is None:
            raise ArchiveBlocked("Restore retry and receipt replay closure is unproven")
        replay_guard(pin_id, operation, manifest)
        at = (now or datetime.now(UTC)).isoformat()
        with self.db:
            self.db.execute(
                "UPDATE managed_snapshot_pin SET state='released',completed_at=?,completion_sha256=? WHERE id=?",
                (at, manifest_sha256, pin_id),
            )
            self.db.execute(
                "INSERT INTO managed_snapshot_pin_event(pin_id,state,at,evidence) VALUES(?,?,?,?)",
                (pin_id, "released", at, manifest_sha256),
            )

    def get(self, identity):
        row = self.db.execute("SELECT receipt FROM bundle WHERE id=?", (identity,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, candidate, state, data, now=None):
        if self.read_only:
            raise ArchiveBlocked("Read API cannot modify archive proofs")
        now = now or datetime.now(UTC)
        old = self.get(candidate.identity)
        allowed = {
            None: {"published"},
            "published": {"published", "verified"},
            "verified": {"verified", "deleting"},
            "deleting": {"deleting", "evicted"},
            "evicted": {"evicted"},
        }
        if state not in allowed[old["state"] if old else None]:
            raise ArchiveBlocked("Invalid archive state transition")
        data = dict(data, state=state, candidate=candidate.identity, policy=POLICY_SHA256)
        if old and old.get("first_verified_at"):
            data["first_verified_at"] = old["first_verified_at"]
        elif state == "verified":
            data["first_verified_at"] = now.isoformat()
        raw = json.dumps(data, sort_keys=True, allow_nan=False)
        with self.db:
            self.db.execute(
                "INSERT INTO bundle VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET receipt=excluded.receipt",
                (
                    candidate.identity,
                    candidate.environment,
                    candidate.source,
                    candidate.organization,
                    candidate.dataset,
                    candidate.start.isoformat(),
                    candidate.end.isoformat(),
                    raw,
                ),
            )
            self.db.execute(
                "INSERT INTO event(at,candidate,state,body) VALUES(?,?,?,?)",
                (now.isoformat(), candidate.identity, state, raw),
            )
        self.publish_read_view()
        return data

    def audit_attempt(self, candidate, state, details, now):
        """Retain failed checks without changing the last valid archive receipt."""
        if self.read_only:
            raise ArchiveBlocked("Read API cannot modify archive proofs")
        if state != "wav_delete_blocked":
            raise ArchiveBlocked("Unsupported archive audit event")
        body = json.dumps(details, sort_keys=True, allow_nan=False)
        with self.db:
            self.db.execute(
                "INSERT INTO event(at,candidate,state,body) VALUES(?,?,?,?)",
                (now.isoformat(), candidate.identity, state, body),
            )

    def publish_read_view(self):
        path = self.root / ("read-view-" + uuid.uuid4().hex + ".sqlite3")
        try:
            with sqlite3.connect(path) as target:
                self.db.backup(target)
                target.execute("PRAGMA journal_mode=DELETE")
            os.chmod(path, 0o600)
            group = os.environ.get("TIERED_ARCHIVE_READ_GID")
            if group:
                os.chown(path, -1, int(group))
                os.chmod(path, 0o640)
            with path.open("rb") as body:
                os.fsync(body.fileno())
            os.replace(path, self.root / "catalog-read.sqlite3")
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            path.unlink(missing_ok=True)

    def scope(self, environment, source, organization, dataset, start, end):
        rows = self.db.execute(
            """SELECT receipt FROM bundle WHERE environment=? AND source=?
           AND organization=? AND dataset=? AND start<? AND end>? ORDER BY start,id""",
            (environment, source, organization, dataset, end.isoformat(), start.isoformat()),
        )
        return [json.loads(row[0]) for row in rows]

    def status(self):
        result = {}
        for (raw,) in self.db.execute("SELECT receipt FROM bundle"):
            state = json.loads(raw)["state"]
            result[state] = result.get(state, 0) + 1
        return result
