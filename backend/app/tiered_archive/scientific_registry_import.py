"""Evidence-bound, one-way import of a complete V3 journal into the authority ledger.

No CLI or implicit execution. Call under the real archive job lock and Source
catalogue lease. Only fixed Science-V3 tables are admitted; this is not a generic
SQLite merge and never grants a writer, retention or snapshot authority.
"""

import base64
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from app.raw_archive import scientific_parts
from app.raw_archive.policy import ArchiveBlocked, checksum

from . import backup as crypto
from . import registry_backup, registry_v5
from .ledger import Ledger, _private_read
from .storage import Store

TABLES = (
    "science_v3_part",
    "science_v3_whole",
    "science_v3_restore",
    "science_v3_scope_epoch",
    "science_v3_scope_history",
)
MAX_DB = 64 * 1024**2
MAX_METADATA = 16 * 1024**2
MAX_INVENTORY = 4 * 1024**2
MAX_HISTORY = 64


def _need(ok, message):
    if not ok:
        raise ArchiveBlocked(message)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _hash_json(value):
    return _sha(_json(value).encode())


def _trusted_root(path):
    path = Path(path)
    _need(
        path.is_absolute() and path.resolve(strict=True) == path,
        "Canonical root ledger path required",
    )
    info = path.lstat()
    _need(
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == os.geteuid()
        and stat.S_IMODE(info.st_mode) == 0o700,
        "Private root ledger directory required",
    )
    for parent in path.parents:
        item = parent.lstat()
        _need(
            stat.S_ISDIR(item.st_mode)
            and item.st_uid in (0, os.geteuid())
            and not item.st_mode & 0o022,
            "Trusted ledger ancestors required",
        )
    return path


def _ledger_identity(root):
    root = _trusted_root(root)
    path = root / "tiered.sqlite3"
    info = path.lstat()
    _need(
        stat.S_ISREG(info.st_mode)
        and info.st_uid == os.geteuid()
        and info.st_nlink == 1
        and stat.S_IMODE(info.st_mode) == 0o600,
        "Private ledger database required",
    )
    return {
        "root": str(root),
        "database": str(path),
        "device": info.st_dev,
        "inode": info.st_ino,
        "owner": info.st_uid,
        "mode": stat.S_IMODE(info.st_mode),
    }


def _live_authority(authority, binding_path, binding_sha256, checkpoint):
    from app.raw_archive.coordination import bound_job_lock
    from app.raw_archive.remote_lease import require_bound_source_lease
    from app.raw_archive.scientific_scope_control import _authority

    _need(
        os.geteuid() == 0 and type(authority) is Ledger and not authority.read_only,
        "Root archive authority required",
    )
    bound_job_lock()
    require_bound_source_lease()
    session, binding = _authority(authority, binding_path, binding_sha256, checkpoint)
    bound_job_lock()
    require_bound_source_lease()
    return session, binding


def _transport(publisher, verifier):
    from app.raw_archive.read_only_box import CatalogReadOnlyBox

    _need(
        type(publisher) is Store
        and type(verifier) is Store
        and type(verifier.transport) is CatalogReadOnlyBox
        and publisher is not verifier
        and publisher.transport is not verifier.transport
        and publisher.identity == verifier.identity,
        "Independent matching read-only archive verifier required",
    )
    return publisher.identity


def _private_proof(path, expected, *, maximum=1024 * 1024):
    path = Path(path)
    raw = _private_read(path, maximum)
    info = path.lstat()
    _need(
        info.st_uid == os.geteuid()
        and stat.S_IMODE(info.st_mode) == 0o400
        and info.st_nlink == 1
        and _sha(raw) == expected,
        "Pinned immutable root evidence required",
    )
    for parent in path.parents:
        info = parent.lstat()
        _need(
            stat.S_ISDIR(info.st_mode)
            and info.st_uid in (0, os.geteuid())
            and not info.st_mode & 0o022,
            "Trusted evidence ancestors required",
        )
    return raw


def _provider_proof(path, expected, terminal_path, terminal_sha):
    value = json.loads(_private_proof(path, expected))
    terminal = json.loads(_private_proof(terminal_path, terminal_sha))
    _need(
        type(value) is dict
        and value.get("provider_readonly_verified") is True
        and value.get("matched_configured_subaccount") is True
        and value.get("request_method") == "GET"
        and type(value.get("mutations")) is int
        and value["mutations"] == 0
        and value.get("credential_values_exported") is False
        and value.get("provider_selected_fields", {})
        .get("access_settings_booleans", {})
        .get("readonly")
        is True,
        "Provider-confirmed read-only evidence required",
    )
    _need(
        type(terminal) is dict
        and set(terminal) == {"exit", "owned_pid", "stderr_sha256", "stdout_sha256"}
        and type(terminal["exit"]) is int
        and terminal["exit"] == 0
        and type(terminal["owned_pid"]) is int
        and terminal["owned_pid"] > 0
        and terminal["stdout_sha256"] == expected
        and registry_v5.sha(terminal["stderr_sha256"]),
        "Provider evidence requires the exact successful owned wait",
    )


def _file_generation(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    _need(
        stat.S_ISREG(info.st_mode)
        and info.st_uid == os.geteuid()
        and info.st_nlink == 1
        and stat.S_IMODE(info.st_mode) == 0o600
        and info.st_size <= MAX_DB,
        "Bounded private SQLite source image required",
    )
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _copy_stable(source, target, expected, checkpoint):
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NOATIME", 0))
    digest, count = hashlib.sha256(), 0
    with os.fdopen(fd, "rb") as body, target.open("xb") as out:
        os.chmod(target, 0o600)
        first = os.fstat(body.fileno())
        _need(
            (first.st_dev, first.st_ino, first.st_size, first.st_mtime_ns, first.st_ctime_ns)
            == expected,
            "Source SQLite generation changed",
        )
        while block := body.read(65536):
            checkpoint()
            count += len(block)
            _need(count <= MAX_DB, "Source SQLite image exceeds budget")
            digest.update(block)
            out.write(block)
        after = os.fstat(body.fileno())
        _need(
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            == expected
            and count == expected[2],
            "Source SQLite image changed",
        )
        out.flush()
        os.fsync(out.fileno())
    return digest.hexdigest()


def _source_snapshot(source_root, authority_root, checkpoint):
    source = _ledger_identity(source_root)
    dbpath = Path(source["database"])
    walpath = Path(str(dbpath) + "-wal")
    journal = Path(str(dbpath) + "-journal")
    _need(not os.path.lexists(journal), "Unknown source SQLite rollback journal")
    before_db, before_wal = _file_generation(dbpath), _file_generation(walpath)
    _need(
        before_db is not None and shutil.disk_usage(authority_root).free >= 2499805184 + 2 * MAX_DB,
        "Bounded import scratch reserve required",
    )
    with tempfile.TemporaryDirectory(prefix="science-import-", dir=authority_root) as folder:
        copied = Path(folder) / "source.sqlite3"
        db_sha = _copy_stable(dbpath, copied, before_db, checkpoint)
        wal_sha = None
        if before_wal is not None:
            wal_sha = _copy_stable(walpath, Path(str(copied) + "-wal"), before_wal, checkpoint)
        _need(
            _file_generation(dbpath) == before_db
            and _file_generation(walpath) == before_wal
            and not os.path.lexists(journal),
            "Source SQLite image changed during capture",
        )
        con = sqlite3.connect(copied.as_uri() + "?mode=ro", uri=True, timeout=3)
        try:
            con.execute("PRAGMA query_only=ON")
            con.execute("BEGIN")
            _need(
                con.execute("SELECT state FROM science_v3_scope WHERE id=1").fetchone()
                == ("current",),
                "Current source Science journal required",
            )
            names = {
                row[0]
                for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'science_v3_%'"
                )
            }
            _need(
                names <= set(TABLES) | {"science_v3_scope"}, "Unknown source Science journal table"
            )
            for name in ("science_v3_reconcile", "sql_deletion_transition"):
                _need(
                    not con.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
                    ).fetchone(),
                    "Unresolved source transition",
                )
            rows = {}
            total_bytes = 0
            for table in TABLES:
                present = con.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
                if table == "science_v3_scope_history" and not present:
                    rows[table] = []
                    continue
                _need(bool(present), "Complete Science journal schema required")
                registry_v5.schema(con, table)
                limit = {
                    "science_v3_part": 46,
                    "science_v3_whole": 1,
                    "science_v3_restore": 46,
                    "science_v3_scope_epoch": 1,
                    "science_v3_scope_history": MAX_HISTORY,
                }[table]
                count = con.execute("SELECT count(*) FROM " + table).fetchone()[0]
                _need(count <= limit, "Science journal row budget exceeded")
                columns = [column[0] for column in registry_v5.TABLES[table]]
                expression = "+".join(
                    'coalesce(length(CAST("' + column + '" AS BLOB)),0)' for column in columns
                )
                total_bytes += con.execute(
                    "SELECT coalesce(sum(" + expression + "),0) FROM " + table
                ).fetchone()[0]
                _need(total_bytes <= MAX_INVENTORY, "Science journal byte budget exceeded")
                rows[table] = [
                    list(item) for item in con.execute("SELECT * FROM " + table + " ORDER BY 1,2")
                ]
            _need(
                len(rows["science_v3_part"]) == len(rows["science_v3_restore"]) == 46
                and len(rows["science_v3_whole"]) == len(rows["science_v3_scope_epoch"]) == 1
                and len(rows["science_v3_scope_history"]) <= MAX_HISTORY,
                "Exact complete Science journal required",
            )
            adapter = SimpleNamespace(db=con)
            for table in TABLES:
                for item in rows[table]:
                    checkpoint()
                    registry_v5.validate(table, item, "production", adapter)
            history = {item[0]: item[1] for item in rows["science_v3_scope_history"]}
            _need(
                len(history) == len(rows["science_v3_scope_history"]),
                "Duplicate Science scope history",
            )
            cursor, seen = rows["science_v3_scope_epoch"][0][1], set()
            while cursor in history:
                _need(cursor not in seen, "Cyclic Science scope history")
                seen.add(cursor)
                cursor = history[cursor]
            _need(len(seen) == len(history), "Unrelated Science scope history")
            con.rollback()
        finally:
            con.close()
        _need(
            _file_generation(dbpath) == before_db
            and _file_generation(walpath) == before_wal
            and not os.path.lexists(journal),
            "Source SQLite image changed during validation",
        )
    encoded = _json(rows).encode()
    _need(len(encoded) <= MAX_INVENTORY, "Science journal inventory exceeds budget")
    return source, db_sha, wal_sha, rows, _sha(encoded), before_db, before_wal


def _validate_whole(
    rows,
    capture,
    index,
    capture_sha,
    index_sha,
    whole_sha,
    destination,
    verifier,
    key,
    folder,
    checkpoint,
):
    epoch = rows["science_v3_scope_epoch"][0]
    _need(
        epoch[3] == capture["capture_id"] and epoch[4] == capture_sha and epoch[5] == index_sha,
        "Science epoch capture/index differs",
    )
    part_refs = {}
    for _token, raw, state, reference in rows["science_v3_part"]:
        binding, ref = json.loads(raw), json.loads(reference)
        pos = binding["position"]
        _need(
            state == "verified"
            and type(pos) is int
            and 0 <= pos < 46
            and pos not in part_refs
            and binding["part"] == index["parts"][pos]
            and binding["destination"] == destination
            and binding["capture_sha256"] == capture_sha
            and binding["index_sha256"] == index_sha
            and binding["capture_id"] == capture["capture_id"],
            "Science part inventory differs",
        )
        part_refs[pos] = ref
    _need(
        set(part_refs) == set(range(46)) and len({x["key"] for x in part_refs.values()}) == 46,
        "Unique complete Science part coverage required",
    )
    token, body_sha, state, raw_ref = rows["science_v3_whole"][0]
    ref = json.loads(raw_ref)
    _need(
        state == "verified"
        and body_sha == whole_sha
        and ref["plaintext_sha256"] == whole_sha
        and ref["bytes"] <= 17 * 1024**2,
        "Verified Science whole required",
    )
    sealed, opened = Path(folder) / "whole.enc", Path(folder) / "whole.json"
    checkpoint()
    verifier.download(ref["key"], sealed, ref["bytes"])
    _need(
        sealed.stat().st_size == ref["bytes"] and checksum(sealed) == ref["sha256"],
        "Science whole ciphertext differs",
    )
    crypto.decrypt(sealed, opened, key, whole_sha, checkpoint=checkpoint)
    _need(opened.stat().st_size <= MAX_METADATA, "Science whole exceeds budget")
    raw = opened.read_bytes()
    _need(_sha(raw) == whole_sha, "Science whole plaintext differs")
    whole = json.loads(raw)
    _need(
        type(whole) is dict
        and set(whole)
        == {
            "format",
            "capture",
            "capture_sha256",
            "index",
            "index_sha256",
            "destination",
            "references",
            "native_restore_verified",
            "parent_fk_closure_proven",
            "cross_database_atomic",
        }
        and whole["format"] == "greenmind.science-whole.v3"
        and whole["capture"] == capture
        and whole["index"] == index
        and whole["capture_sha256"] == capture_sha
        and whole["index_sha256"] == index_sha
        and whole["destination"] == destination
        and whole["references"] == [part_refs[i] for i in range(46)]
        and whole["native_restore_verified"] is False
        and whole["parent_fk_closure_proven"] is False
        and whole["cross_database_atomic"] is False,
        "Science whole does not cover frozen journal",
    )
    _need(
        {item[1] for item in rows["science_v3_restore"]} == set(range(46))
        and all(item[0] == whole_sha for item in rows["science_v3_restore"]),
        "Complete native restore audit required",
    )
    return epoch


def _epoch_frame(path, epoch, binding, capture, index, capture_sha, index_sha):
    frame = json.loads(_private_proof(path, epoch[6]))
    fields = {
        "format",
        "source",
        "capture_sha256",
        "index_sha256",
        "capture_id",
        "scope_sha256",
        "native_files",
        "part_count",
        "hostname",
        "machine_sha256",
        "boot_id",
        "uid",
        "lease_session",
        "source_lock",
        "producer_sha256",
        "parts_code_sha256",
        "capture_code_sha256",
        "observed_at",
    }
    expected_files = [
        {
            "table": item["table"],
            "rows": item["rows"],
            "bytes": item["bytes"],
            "sha256": item["sha256"],
        }
        for item in capture["files"]
    ]
    lock = frame.get("source_lock", {}) if type(frame) is dict else {}
    try:
        observed = datetime.fromisoformat(frame["observed_at"])
        finished = datetime.fromisoformat(capture["finished_at"])
        valid_time = (
            observed.utcoffset() is not None
            and finished.utcoffset() is not None
            and finished <= observed <= datetime.now(UTC)
        )
    except (KeyError, ValueError, TypeError):
        valid_time = False
    _need(
        type(frame) is dict
        and set(frame) == fields
        and frame["format"] == "greenmind.science-source-handoff.v1"
        and frame["source"] == "gateway"
        and type(frame["uid"]) is int
        and frame["uid"] == 0
        and frame["capture_sha256"] == capture_sha
        and frame["index_sha256"] == index_sha
        and frame["capture_id"] == capture["capture_id"]
        and frame["scope_sha256"] == capture["scope_sha256"]
        and frame["native_files"] == expected_files
        and frame["part_count"] == len(index["parts"]) == 46
        and frame["lease_session"] == epoch[2]
        and frame["hostname"] == binding["hostname"]
        and frame["machine_sha256"] == binding["machine_sha256"]
        and type(lock) is dict
        and set(lock)
        == {"device", "inode", "pid", "uid", "start_ticks", "command_sha256", "executable_sha256"}
        and lock["device"] == binding["lock_device"]
        and lock["inode"] == binding["lock_inode"]
        and lock["uid"] == binding["lease_uid"]
        and lock["command_sha256"] == binding["lease_command_sha256"]
        and lock["executable_sha256"] == binding["lease_executable_sha256"]
        and type(lock["pid"]) is int
        and lock["pid"] > 1
        and type(lock["start_ticks"]) is int
        and lock["start_ticks"] > 0
        and all(
            registry_v5.sha(frame[name])
            for name in ("producer_sha256", "parts_code_sha256", "capture_code_sha256")
        )
        and type(frame["boot_id"]) is str
        and re.fullmatch(r"[0-9a-f-]{36}", frame["boot_id"])
        and valid_time,
        "Original source epoch handoff differs",
    )


def _bootstrap_receipt(path, expected, epoch, history):
    receipt = json.loads(_private_proof(path, expected))
    chain = {item[0]: item[1] for item in history}
    genesis = epoch[1]
    while genesis in chain:
        genesis = chain[genesis]
    result = receipt.get("result", {}) if type(receipt) is dict else {}
    payload = result.get("result", {}) if type(result) is dict else {}
    frame = receipt.get("frame_proof", {}) if type(receipt) is dict else {}
    _need(
        type(receipt) is dict
        and receipt.get("action") == "bootstrap"
        and type(receipt.get("owned_worker_wait_exit")) is int
        and receipt["owned_worker_wait_exit"] == 0
        and receipt.get("worker_generation_gone") is True
        and receipt.get("worker_cgroup_gone") is True
        and receipt.get("archive_accepted") is False
        and receipt.get("wip_not_release") is True
        and type(frame) is dict
        and type(frame.get("source_owned_wait_exit")) is int
        and frame["source_owned_wait_exit"] == 0
        and frame.get("frame_sha256") == epoch[6]
        and type(payload) is dict
        and payload.get("status") == "SCIENCE_SCOPE_CURRENT"
        and payload.get("scope_epoch") == genesis
        and payload.get("capture_id") == epoch[3]
        and payload.get("sql_scope") == "HOLD"
        and result.get("writer_allowed") is False
        and result.get("archive_accepted") is False
        and result.get("sql_allowed") is False,
        "Original Science bootstrap owned-wait receipt differs",
    )


def _collect(
    authority,
    source_root,
    *,
    source_binding_path,
    source_binding_sha256,
    provider_evidence_path,
    provider_evidence_sha256,
    provider_terminal_path,
    provider_terminal_sha256,
    source_epoch_frame_path,
    bootstrap_receipt_path,
    bootstrap_receipt_sha256,
    capture_root,
    capture_sha256,
    index_file,
    index_sha256,
    whole_sha256,
    publisher,
    verifier,
    key,
    checkpoint,
):
    _need(
        callable(checkpoint)
        and type(key) is bytes
        and len(key) == 32
        and all(
            type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value)
            for value in (
                source_binding_sha256,
                provider_evidence_sha256,
                provider_terminal_sha256,
                bootstrap_receipt_sha256,
                capture_sha256,
                index_sha256,
                whole_sha256,
            )
        ),
        "Pinned import parameters required",
    )
    session, binding = _live_authority(
        authority, source_binding_path, source_binding_sha256, checkpoint
    )
    destination = _transport(publisher, verifier)
    _provider_proof(
        provider_evidence_path,
        provider_evidence_sha256,
        provider_terminal_path,
        provider_terminal_sha256,
    )
    target_identity = _ledger_identity(authority.root)
    database_rows = [
        row for row in authority.db.execute("PRAGMA database_list") if row[1] == "main"
    ]
    _need(
        len(database_rows) == 1
        and Path(database_rows[0][2]).resolve(strict=True) == Path(target_identity["database"]),
        "Authority connection does not match its root ledger",
    )
    source_identity = _ledger_identity(source_root)
    _need(
        (target_identity["device"], target_identity["inode"])
        != (source_identity["device"], source_identity["inode"]),
        "Separate Science source and authority ledger required",
    )
    _trusted_root(capture_root)
    _private_proof(Path(capture_root) / "capture.json", capture_sha256, maximum=MAX_METADATA)
    _private_proof(index_file, index_sha256, maximum=MAX_METADATA)
    capture, index = scientific_parts.load_index(
        capture_root,
        capture_sha256,
        index_file,
        index_sha256,
        checkpoint=checkpoint,
        verify_files=False,
    )
    _need(len(index["parts"]) == 46, "Exact 46-part Science scope required")
    source_identity, db_sha, wal_sha, rows, inventory_sha, db_generation, wal_generation = (
        _source_snapshot(source_root, authority.root, checkpoint)
    )
    with tempfile.TemporaryDirectory(prefix="science-whole-", dir=authority.root) as folder:
        epoch = _validate_whole(
            rows,
            capture,
            index,
            capture_sha256,
            index_sha256,
            whole_sha256,
            destination,
            verifier,
            key,
            folder,
            checkpoint,
        )
    _epoch_frame(
        source_epoch_frame_path, epoch, binding, capture, index, capture_sha256, index_sha256
    )
    _bootstrap_receipt(
        bootstrap_receipt_path, bootstrap_receipt_sha256, epoch, rows["science_v3_scope_history"]
    )
    current, current_binding = _live_authority(
        authority, source_binding_path, source_binding_sha256, checkpoint
    )
    _need(
        current == session and current_binding == binding,
        "Source lease changed during Science import inspection",
    )
    source_path = Path(source_identity["database"])
    _need(
        _file_generation(source_path) == db_generation
        and _file_generation(Path(str(source_path) + "-wal")) == wal_generation
        and not os.path.lexists(str(source_path) + "-journal"),
        "Source Science journal changed after validation",
    )
    manifest = {
        "format": "greenmind.science-registry-import-manifest.v1",
        "environment": "production",
        "source_ledger": source_identity,
        "authority_ledger": target_identity,
        "source_database_sha256": db_sha,
        "source_wal_sha256": wal_sha,
        "source_database_generation": list(db_generation),
        "source_wal_generation": list(wal_generation) if wal_generation is not None else None,
        "source_binding_sha256": source_binding_sha256,
        "source_machine_sha256": binding["machine_sha256"],
        "provider_evidence_sha256": provider_evidence_sha256,
        "provider_terminal_sha256": provider_terminal_sha256,
        "bootstrap_receipt_sha256": bootstrap_receipt_sha256,
        "encryption_key_sha256": _sha(key),
        "observation_session": session,
        "scope_epoch": epoch[1],
        "scope_source_session": epoch[2],
        "capture_id": epoch[3],
        "capture_sha256": capture_sha256,
        "index_sha256": index_sha256,
        "scope_frame_sha256": epoch[6],
        "whole_sha256": whole_sha256,
        "destination": destination,
        "inventory_sha256": inventory_sha,
        "part_count": 46,
        "audit_count": 46,
    }
    return manifest, rows


def prepare_manifest(authority, source_root, **kwargs):
    """Inspect real bound evidence; return bytes for a new root-0400 manifest.

    No ledger mutation occurs. The caller must fsync the exact bytes, then pin
    their returned SHA before invoking ``import_manifest`` under a live lease.
    """
    manifest, _ = _collect(authority, source_root, **kwargs)
    raw = (_json(manifest) + "\n").encode()
    return {"manifest": raw, "sha256": _sha(raw)}


def write_manifest(path, prepared):
    """Persist one newly observed manifest as a root-0400 immutable input."""
    path = Path(path)
    _need(
        os.geteuid() == 0
        and path.is_absolute()
        and path.resolve(strict=False) == path
        and type(prepared) is dict
        and set(prepared) == {"manifest", "sha256"}
        and type(prepared["manifest"]) is bytes
        and _sha(prepared["manifest"]) == prepared["sha256"]
        and len(prepared["manifest"]) <= 1024 * 1024,
        "Exact observed root manifest required",
    )
    for parent in path.parents:
        info = parent.lstat()
        _need(
            stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
            "Trusted root manifest parent required",
        )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    with os.fdopen(fd, "wb") as body:
        body.write(prepared["manifest"])
        body.flush()
        os.fsync(body.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return prepared["sha256"]


def _initialize(db):
    _need(
        not db.in_transaction and db.execute("PRAGMA synchronous").fetchone()[0] >= 2,
        "Idle durable authority ledger required",
    )
    db.execute("""CREATE TABLE IF NOT EXISTS science_registry_import_intent (
        id TEXT PRIMARY KEY, manifest_sha256 TEXT NOT NULL, inventory_sha256 TEXT NOT NULL,
        state TEXT NOT NULL, backup_reference TEXT)""")
    expected = (
        ("id", "TEXT", 0, 1),
        ("manifest_sha256", "TEXT", 1, 0),
        ("inventory_sha256", "TEXT", 1, 0),
        ("state", "TEXT", 1, 0),
        ("backup_reference", "TEXT", 0, 0),
    )
    actual = tuple(
        (x[1], x[2].upper(), x[3], x[5])
        for x in db.execute("PRAGMA table_info(science_registry_import_intent)")
    )
    _need(actual == expected, "Science import intent schema differs")
    db.commit()


def _import_id(manifest):
    return _sha(
        (
            "science-v3-import:"
            + manifest["scope_epoch"]
            + ":"
            + manifest["capture_sha256"]
            + ":"
            + manifest["index_sha256"]
            + ":"
            + manifest["whole_sha256"]
        ).encode()
    )


_EVIDENCE_FIELDS = {
    "source_binding": ("source_binding_path", "source_binding_sha256"),
    "provider": ("provider_evidence_path", "provider_evidence_sha256"),
    "provider_terminal": ("provider_terminal_path", "provider_terminal_sha256"),
    "source_epoch_frame": ("source_epoch_frame_path", "scope_frame_sha256"),
    "bootstrap_receipt": ("bootstrap_receipt_path", "bootstrap_receipt_sha256"),
}


def _evidence_plain(raw_manifest, manifest, kwargs):
    files = {"manifest": base64.b64encode(raw_manifest).decode("ascii")}
    for name, (path_key, hash_key) in _EVIDENCE_FIELDS.items():
        raw = _private_proof(kwargs[path_key], manifest[hash_key], maximum=128 * 1024)
        files[name] = base64.b64encode(raw).decode("ascii")
    raw = _json({"format": "greenmind.science-import-evidence.v1", "files": files}).encode()
    _need(len(raw) <= 384 * 1024, "Science import evidence exceeds budget")
    return raw


def _seal_evidence(raw, key, identity):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(12)
    sealed = nonce + AESGCM(key).encrypt(nonce, raw, identity.encode("ascii"))
    return {
        "format": "aes-256-gcm-science-evidence.v1",
        "sha256": _sha(sealed),
        "plaintext_sha256": _sha(raw),
        "bytes": len(sealed),
        "ciphertext_base64": base64.b64encode(sealed).decode("ascii"),
    }


def verify_provenance_evidence(record, key):
    """Authenticate archived original evidence, without exposing its contents."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        _need(
            type(key) is bytes and len(key) == 32 and _sha(key) == record["encryption_key_sha256"],
            "Science import evidence key differs",
        )
        envelope = record["evidence"]
        sealed = base64.b64decode(envelope["ciphertext_base64"], validate=True)
        _need(
            len(sealed) == envelope["bytes"]
            and _sha(sealed) == envelope["sha256"]
            and 28 <= len(sealed) <= 384 * 1024 + 28,
            "Science import evidence ciphertext differs",
        )
        identity = _sha(
            (
                "science-v3-import:"
                + record["scope_epoch"]
                + ":"
                + record["capture_sha256"]
                + ":"
                + record["index_sha256"]
                + ":"
                + record["whole_sha256"]
            ).encode()
        )
        plain = AESGCM(key).decrypt(sealed[:12], sealed[12:], identity.encode("ascii"))
        _need(
            _sha(plain) == envelope["plaintext_sha256"], "Science import evidence plaintext differs"
        )
        package = json.loads(plain)
        _need(
            type(package) is dict
            and set(package) == {"format", "files"}
            and package["format"] == "greenmind.science-import-evidence.v1"
            and type(package["files"]) is dict
            and set(package["files"]) == set(_EVIDENCE_FIELDS) | {"manifest"}
            and _json(package).encode() == plain,
            "Science import evidence package invalid",
        )
        files = {
            name: base64.b64decode(value, validate=True) for name, value in package["files"].items()
        }
        _need(
            _sha(files["manifest"]) == record["manifest_sha256"],
            "Science import original manifest differs",
        )
        for name, (_, hash_key) in _EVIDENCE_FIELDS.items():
            _need(_sha(files[name]) == record[hash_key], "Science import original evidence differs")
        original = json.loads(files["manifest"])
        _need(
            original["inventory_sha256"] == record["inventory_sha256"]
            and original["scope_epoch"] == record["scope_epoch"]
            and original["observation_session"] == record["observation_session"],
            "Science import original manifest binding differs",
        )
        return True
    except ArchiveBlocked:
        raise
    except Exception:
        raise ArchiveBlocked("Science import evidence authentication failed") from None


def _provenance(manifest, manifest_sha, evidence):
    record = {
        "format": "greenmind.science-registry-import.v1",
        "manifest_sha256": manifest_sha,
        "inventory_sha256": manifest["inventory_sha256"],
        "source_ledger_identity_sha256": _hash_json(manifest["source_ledger"]),
        "authority_ledger_identity_sha256": _hash_json(manifest["authority_ledger"]),
        "destination_sha256": _sha(manifest["destination"].encode()),
        "evidence": evidence,
    }
    for name in (
        "source_machine_sha256",
        "source_binding_sha256",
        "provider_evidence_sha256",
        "provider_terminal_sha256",
        "bootstrap_receipt_sha256",
        "encryption_key_sha256",
        "source_database_sha256",
        "source_wal_sha256",
        "observation_session",
        "scope_epoch",
        "scope_source_session",
        "scope_frame_sha256",
        "capture_id",
        "capture_sha256",
        "index_sha256",
        "whole_sha256",
        "part_count",
        "audit_count",
    ):
        record[name] = manifest[name]
    return [_import_id(manifest), manifest_sha, manifest["inventory_sha256"], _json(record)]


def _existing_table(db, table):
    return bool(
        db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    )


def _require_no_owned_snapshots(db):
    if _existing_table(db, "managed_snapshot"):
        _need(
            not db.execute(
                "SELECT 1 FROM managed_snapshot WHERE state IS NULL OR state!='deleted' LIMIT 1"
            ).fetchone(),
            "Existing owned snapshots require separate post-import coverage plan",
        )
    if _existing_table(db, "managed_snapshot_pin"):
        _need(
            not db.execute(
                "SELECT 1 FROM managed_snapshot_pin WHERE state IS NULL OR state!='released' LIMIT 1"
            ).fetchone(),
            "Active or unknown managed snapshot pins block import",
        )


def _require_snapshot_scope_hold(db):
    if _existing_table(db, "managed_snapshot_scope"):
        rows = db.execute("SELECT id,recovery_state FROM managed_snapshot_scope").fetchall()
        _need(
            not rows or rows == [(1, "needs_reconcile")],
            "Managed snapshot scope must remain unreconciled",
        )


def _recheck_generations(authority, manifest):
    _need(
        _ledger_identity(authority.root) == manifest["authority_ledger"]
        and _ledger_identity(manifest["source_ledger"]["root"]) == manifest["source_ledger"],
        "Science import ledger identity changed",
    )
    path = Path(manifest["source_ledger"]["database"])
    db_generation = tuple(manifest["source_database_generation"])
    wal_generation = (
        tuple(manifest["source_wal_generation"])
        if manifest["source_wal_generation"] is not None
        else None
    )
    _need(
        _file_generation(path) == db_generation
        and _file_generation(Path(str(path) + "-wal")) == wal_generation
        and not os.path.lexists(str(path) + "-journal"),
        "Science source generation changed before commit",
    )


def _prepare_intent(db, manifest, manifest_sha, authorize):
    identity, inventory_sha = _import_id(manifest), manifest["inventory_sha256"]
    _initialize(db)
    try:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(
            "SELECT id,manifest_sha256,inventory_sha256,state,backup_reference FROM science_registry_import_intent"
        ).fetchall()
        _need(len(existing) <= 1, "Multiple Science import intents are unknown")
        if existing:
            item = existing[0]
            _need(
                item[:3] == (identity, manifest_sha, inventory_sha)
                and item[3] in {"prepared", "applied", "complete"},
                "Conflicting Science import intent",
            )
            if item[3] != "complete":
                _require_no_owned_snapshots(db)
                _require_snapshot_scope_hold(db)
            authorize()
            db.commit()
            return item[3], item[4]
        _require_no_owned_snapshots(db)
        names = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'science_v3_%'"
            )
        }
        _need(
            names <= set(TABLES) | {"science_v3_scope", "science_v3_import_provenance"},
            "Unknown authority Science journal table",
        )
        for table in (*TABLES, "science_v3_import_provenance"):
            if _existing_table(db, table):
                registry_v5.schema(db, table)
                _need(
                    not db.execute("SELECT 1 FROM " + table + " LIMIT 1").fetchone(),
                    "Existing Science journal cannot be merged",
                )
        if _existing_table(db, "science_v3_scope"):
            scope = db.execute("SELECT id,state FROM science_v3_scope").fetchall()
            _need(
                not scope or scope == [(1, "needs_reconcile")],
                "Existing Science scope cannot be merged",
            )
        if _existing_table(db, "managed_snapshot_scope"):
            scope = db.execute("SELECT id,recovery_state FROM managed_snapshot_scope").fetchall()
            _need(
                not scope
                or len(scope) == 1
                and scope[0][0] == 1
                and scope[0][1] in {"current", "needs_reconcile"},
                "Unknown managed snapshot scope",
            )
            db.execute(
                "UPDATE managed_snapshot_scope SET recovery_state='needs_reconcile' WHERE id=1"
            )
        db.execute(
            "INSERT INTO science_registry_import_intent VALUES(?,?,?,'prepared',NULL)",
            (identity, manifest_sha, inventory_sha),
        )
        authorize()
        db.commit()
        return "prepared", None
    except BaseException:
        db.rollback()
        raise


def _apply_rows(authority, manifest, manifest_sha, rows, evidence, checkpoint, authorize):
    db = authority.db
    try:
        db.execute("BEGIN IMMEDIATE")
        _require_no_owned_snapshots(db)
        _require_snapshot_scope_hold(db)
        intent = db.execute(
            "SELECT manifest_sha256,inventory_sha256,state FROM science_registry_import_intent WHERE id=?",
            (_import_id(manifest),),
        ).fetchone()
        _need(
            intent == (manifest_sha, manifest["inventory_sha256"], "prepared"),
            "Science import intent changed",
        )
        for table in (*TABLES, "science_v3_import_provenance"):
            db.execute(registry_v5.DDL[table])
            registry_v5.schema(db, table)
            _need(
                not db.execute("SELECT 1 FROM " + table + " LIMIT 1").fetchone(),
                "Conflicting Science import rows",
            )
        if not _existing_table(db, "science_v3_scope"):
            db.execute(
                "CREATE TABLE science_v3_scope (id INTEGER PRIMARY KEY CHECK(id=1), state TEXT NOT NULL)"
            )
        scope = db.execute("SELECT id,state FROM science_v3_scope").fetchall()
        _need(not scope or scope == [(1, "needs_reconcile")], "Science scope cannot be promoted")
        if not scope:
            db.execute("INSERT INTO science_v3_scope VALUES(1,'needs_reconcile')")
        for table in TABLES:
            for item in rows[table]:
                checkpoint()
                registry_v5.validate(table, item, "production", authority)
                db.execute(
                    "INSERT INTO " + table + " VALUES(" + ",".join("?" for _ in item) + ")", item
                )
        provenance = _provenance(manifest, manifest_sha, evidence)
        registry_v5.validate("science_v3_import_provenance", provenance, "production", authority)
        db.execute("INSERT INTO science_v3_import_provenance VALUES(?,?,?,?)", provenance)
        db.execute(
            "UPDATE science_registry_import_intent SET state='applied' WHERE id=? AND state='prepared'",
            (_import_id(manifest),),
        )
        _need(db.execute("SELECT changes()").fetchone()[0] == 1, "Science import intent raced")
        checkpoint()
        _recheck_generations(authority, manifest)
        authorize()
        db.commit()
    except BaseException:
        db.rollback()
        raise


def _verify_import_rows(
    ledger, manifest, manifest_sha, rows, evidence, key, *, require_unreconciled=True
):
    for table in TABLES:
        _need(_existing_table(ledger.db, table), "Imported Science table missing")
        registry_v5.schema(ledger.db, table)
        actual = [
            list(item) for item in ledger.db.execute("SELECT * FROM " + table + " ORDER BY 1,2")
        ]
        _need(actual == rows[table], "Imported Science rows differ")
    provenance = _provenance(manifest, manifest_sha, evidence)
    _need(
        _existing_table(ledger.db, "science_v3_import_provenance")
        and ledger.db.execute("SELECT * FROM science_v3_import_provenance").fetchall()
        == [tuple(provenance)],
        "Imported Science provenance differs",
    )
    registry_v5.validate("science_v3_import_provenance", provenance, "production", ledger)
    verify_provenance_evidence(json.loads(provenance[3]), key)
    scope = ledger.db.execute("SELECT state FROM science_v3_scope WHERE id=1").fetchone()
    _need(
        scope == ("needs_reconcile",) or not require_unreconciled and scope == ("current",),
        "Imported Science scope state is unknown",
    )
    return scope[0]


def import_manifest(authority, source_root, *, manifest_path, manifest_sha256, **kwargs):
    """Atomically import fixed Science rows, then independently restore V5 backup.

    Every retry revalidates the original immutable manifest and current leases.
    Incomplete stages remain durable; no unknown state is promoted to current.
    """
    raw = _private_proof(manifest_path, manifest_sha256)
    try:
        manifest = json.loads(raw)
    except (TypeError, ValueError):
        raise ArchiveBlocked("Science import manifest invalid") from None
    _need(raw == (_json(manifest) + "\n").encode(), "Canonical Science import manifest required")
    observed, rows = _collect(authority, source_root, **kwargs)
    # A retry has a fresh real lease; the original observation remains in provenance.
    _need(
        {k: v for k, v in observed.items() if k != "observation_session"}
        == {k: v for k, v in manifest.items() if k != "observation_session"},
        "Pinned Science import evidence changed",
    )
    if observed["observation_session"] != manifest["observation_session"]:
        db = authority.db
        prior = (
            db.execute(
                "SELECT manifest_sha256,state FROM science_registry_import_intent WHERE id=?",
                (_import_id(manifest),),
            ).fetchone()
            if _existing_table(db, "science_registry_import_intent")
            else None
        )
        _need(
            prior is not None
            and prior[0] == manifest_sha256
            and prior[1] in {"prepared", "applied", "complete"},
            "Original source observation lacks durable import intent",
        )
    checkpoint = kwargs["checkpoint"]

    def authorize():
        session, binding = _live_authority(
            authority, kwargs["source_binding_path"], kwargs["source_binding_sha256"], checkpoint
        )
        _need(
            session == observed["observation_session"]
            and binding.get("machine_sha256") == manifest["source_machine_sha256"],
            "Live source authority changed during Science import",
        )

    _recheck_generations(authority, manifest)
    state, saved_reference = _prepare_intent(authority.db, manifest, manifest_sha256, authorize)
    plain_evidence = _evidence_plain(raw, manifest, kwargs)
    if state == "prepared":
        evidence = _seal_evidence(plain_evidence, kwargs["key"], _import_id(manifest))
        _apply_rows(authority, manifest, manifest_sha256, rows, evidence, checkpoint, authorize)
        state = "applied"
    else:
        existing = authority.db.execute(
            "SELECT record FROM science_v3_import_provenance WHERE id=?", (_import_id(manifest),)
        ).fetchone()
        _need(existing is not None, "Science import evidence missing")
        evidence = json.loads(existing[0])["evidence"]
        _need(
            evidence["plaintext_sha256"] == _sha(plain_evidence), "Science import evidence changed"
        )
    scope_state = _verify_import_rows(
        authority,
        manifest,
        manifest_sha256,
        rows,
        evidence,
        kwargs["key"],
        require_unreconciled=state != "complete",
    )
    if state == "complete":
        _need(saved_reference is not None, "Completed Science import lacks backup reference")
        return {
            "status": "ALREADY_IMPORTED",
            "inventory_sha256": manifest["inventory_sha256"],
            "registry_reference": json.loads(saved_reference),
            "science_scope": scope_state,
        }
    checkpoint()
    _recheck_generations(authority, manifest)
    _require_no_owned_snapshots(authority.db)
    _require_snapshot_scope_hold(authority.db)
    reference = registry_backup.backup(authority, kwargs["publisher"], checkpoint=checkpoint)
    _need(
        type(reference) is dict and reference.get("status") != "empty",
        "Science registry backup missing",
    )
    with tempfile.TemporaryDirectory(prefix="science-import-proof-", dir=authority.root) as folder:
        restored = Ledger(Path(folder) / "restored")
        try:
            registry_backup.restore(
                reference,
                restored,
                kwargs["verifier"],
                checkpoint=checkpoint,
                expected_environment="production",
            )
            _verify_import_rows(restored, manifest, manifest_sha256, rows, evidence, kwargs["key"])
        finally:
            restored.close()
    authorize()
    _recheck_generations(authority, manifest)
    _verify_import_rows(authority, manifest, manifest_sha256, rows, evidence, kwargs["key"])
    with authority.db:
        _require_no_owned_snapshots(authority.db)
        _require_snapshot_scope_hold(authority.db)
        authority.db.execute(
            "UPDATE science_registry_import_intent SET state='complete',backup_reference=? WHERE id=? AND state='applied'",
            (_json(reference), _import_id(manifest)),
        )
        _need(
            authority.db.execute("SELECT changes()").fetchone()[0] == 1,
            "Science import completion changed",
        )
        authorize()
    return {
        "status": "IMPORTED_AND_READBACK_VERIFIED",
        "inventory_sha256": manifest["inventory_sha256"],
        "registry_reference": reference,
        "science_scope": "needs_reconcile",
        "writer_allowed": False,
        "snapshot_scope_promoted": False,
        "sql_scope_changed": False,
    }
