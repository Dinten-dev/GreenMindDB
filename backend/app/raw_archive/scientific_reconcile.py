"""Bounded independent Science-V3 remote readback after ledger restoration.

One call authenticates the whole manifest and one ordered part. No publishing,
source writes, source capture resume, snapshot rotation or SQL enablement.
"""

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.tiered_archive import backup, registry_v5

from . import scientific_capture as capture
from . import scientific_parts as parts
from . import scientific_scope_control as control
from . import scientific_v3 as v3
from .policy import ArchiveBlocked, checksum
from .scientific_scope_v3 import SOURCE_SCOPE, SOURCE_SCOPE_SHA256


def _need(value, message):
    if not value:
        raise ArchiveBlocked(message)


def _inventory(ledger, *, expected_state="needs_reconcile", previous_epoch=None):
    db = ledger.db
    _need(
        db.execute("SELECT state FROM science_v3_scope WHERE id=1").fetchone() == (expected_state,),
        "Science scope must remain in reconciliation",
    )
    for name in ("science_v3_part", "science_v3_whole", "science_v3_scope_epoch"):
        registry_v5.schema(db, name)
    epoch = db.execute("SELECT * FROM science_v3_scope_epoch WHERE id=1").fetchone()
    _need(epoch is not None, "Restored science scope epoch required")
    registry_v5.validate("science_v3_scope_epoch", list(epoch), "production", ledger)
    parts_rows = db.execute(
        "SELECT token,binding,state,reference FROM science_v3_part ORDER BY json_extract(binding,'$.position')"
    ).fetchall()
    roots = db.execute("SELECT token,body_sha256,state,reference FROM science_v3_whole").fetchall()
    _need(
        2 <= len(parts_rows) <= 10000 and len(roots) == 1,
        "One whole and complete bounded Science part inventory required",
    )
    root = roots[0]
    registry_v5.validate("science_v3_whole", list(root), "production", ledger)
    immutable = hashlib.sha256()
    original_epoch = list(epoch)
    if previous_epoch is not None:
        original_epoch[1] = previous_epoch
    immutable.update(v3._canonical(original_epoch).encode())
    immutable.update(v3._canonical([root[0], root[1], root[3]]).encode())
    ordered = []
    for position, row in enumerate(parts_rows):
        registry_v5.validate("science_v3_part", list(row), "production", ledger)
        binding, reference = json.loads(row[1]), json.loads(row[3])
        _need(
            binding["position"] == position
            and binding["capture_id"] == epoch[3]
            and binding["capture_sha256"] == epoch[4]
            and binding["index_sha256"] == epoch[5],
            "Restored Science inventory position or epoch differs",
        )
        immutable.update(v3._canonical([row[0], row[1], row[3]]).encode())
        ordered.append((row, binding, reference))
    return epoch, root, ordered, immutable.hexdigest()


def _whole(body, epoch, ordered, destination):
    _need(
        type(body) is dict
        and set(body)
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
        and body["format"] == "greenmind.science-whole.v3"
        and body["destination"] == destination
        and body["capture_sha256"] == epoch[4]
        and body["index_sha256"] == epoch[5]
        and body["native_restore_verified"] is False
        and body["parent_fk_closure_proven"] is False
        and body["cross_database_atomic"] is False,
        "Authenticated whole inventory scope differs",
    )
    proof, index = body["capture"], body["index"]
    _need(
        type(proof) is dict
        and proof.get("format") == capture.FORMAT
        and proof.get("complete") is True
        and proof.get("source") == "gateway"
        and proof.get("capture_id") == epoch[3]
        and proof.get("scope") == SOURCE_SCOPE
        and proof.get("scope_sha256") == SOURCE_SCOPE_SHA256
        and hashlib.sha256((v3._canonical(proof) + "\n").encode()).hexdigest() == epoch[4]
        and hashlib.sha256((v3._canonical(index) + "\n").encode()).hexdigest() == epoch[5],
        "Authenticated Science capture or index differs",
    )
    parts.validate_index(proof, index, epoch[4])
    _need(
        len(index["parts"]) == len(ordered)
        and body["references"] == [item[2] for item in ordered]
        and all(
            index["parts"][position] == item[1]["part"] for position, item in enumerate(ordered)
        ),
        "Whole inventory does not cover exact ordered part receipts",
    )
    return proof


def reconcile_step(
    ledger,
    authority_ledger,
    verifier,
    key,
    workdir,
    position,
    *,
    source_binding_path,
    source_binding_sha256,
    lease_frame_path,
    lease_frame_sha256,
    checkpoint,
):
    """Authenticate one part from real remote readback; finish only after all parts.

    The caller supplies a separately authenticated GET-only verifier and real
    Archive→Source locks. No Source data are read. Repeated calls are safe.
    """
    from app.raw_archive.read_only_box import ReadOnlyBox
    from app.tiered_archive.sql_runtime import ScopedReadStore

    _need(
        os.geteuid() == 0
        and type(verifier) is ScopedReadStore
        and type(verifier.transport) is ReadOnlyBox
        and type(key) is bytes
        and len(key) in (16, 24, 32)
        and callable(checkpoint),
        "Root scoped verifier and private AES key required",
    )
    _need(
        ledger.root.resolve() != authority_ledger.root.resolve(),
        "Separate restored science ledger required",
    )
    session, source_binding = control._authority(
        authority_ledger, source_binding_path, source_binding_sha256, checkpoint
    )
    control.validate_lease_handoff(lease_frame_path, lease_frame_sha256, source_binding, session)
    if ledger.db.execute("SELECT state FROM science_v3_scope WHERE id=1").fetchone() == (
        "current",
    ):
        current_epoch = ledger.db.execute(
            "SELECT epoch FROM science_v3_scope_epoch WHERE id=1"
        ).fetchone()
        need_history = ledger.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='science_v3_scope_history'"
        ).fetchone()
        _need(
            current_epoch and need_history, "Current scope lacks a completed reconciliation audit"
        )
        history = ledger.db.execute(
            "SELECT epoch,previous_epoch,inventory_sha256,authority_session,created_at FROM science_v3_scope_history WHERE epoch=?",
            current_epoch,
        ).fetchone()
        _need(history is not None, "Current scope lacks a completed reconciliation audit")
        registry_v5.validate("science_v3_scope_history", list(history), "production", ledger)
        _, root, checked, fingerprint = _inventory(
            ledger, expected_state="current", previous_epoch=history[1]
        )
        _need(
            type(position) is int
            and 0 <= position < len(checked)
            and fingerprint == history[2]
            and root[2] == "verified"
            and all(item[0][2] == "verified" for item in checked)
            and not ledger.db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='science_v3_reconcile'"
            ).fetchone(),
            "Completed Science scope audit differs",
        )
        return {
            "status": "SCIENCE_SCOPE_CURRENT",
            "verified_parts": len(checked),
            "total_parts": len(checked),
            "sql_scope": "HOLD",
            "writer_allowed": False,
        }
    epoch, root, ordered, inventory_sha = _inventory(ledger)
    _need(
        type(position) is int and 0 <= position < len(ordered),
        "Exact restored part position required",
    )
    _need(
        all(item[1]["destination"] == verifier.identity for item in ordered),
        "Archive destination differs",
    )
    workdir = v3._directory(workdir)
    shutil.disk_usage(workdir)  # Require an existing checked filesystem.
    from app.tiered_archive.storage import disk_reserve

    disk_reserve(workdir, 160 * 1024**2)
    from app.raw_archive.coordination import bound_job_lock

    bound_job_lock()
    with tempfile.TemporaryDirectory(prefix="science-reconcile-", dir=workdir) as folder:
        folder = Path(folder)
        root_ref = json.loads(root[3])
        root_cipher, root_plain = folder / "whole.bin", folder / "whole.json"
        checkpoint()
        verifier.download(root_ref["key"], root_cipher, root_ref["bytes"])
        _need(
            root_cipher.stat().st_size == root_ref["bytes"]
            and checksum(root_cipher) == root_ref["sha256"],
            "Whole ciphertext readback differs",
        )
        backup.decrypt(root_cipher, root_plain, key, root[1], checkpoint=checkpoint)
        _need(root_plain.stat().st_size <= 16 * 1024**2, "Whole inventory exceeds budget")
        whole = json.loads(root_plain.read_bytes())
        proof = _whole(whole, epoch, ordered, verifier.identity)
        v3._drop_cache(root_cipher)
        v3._drop_cache(root_plain)
        row, binding, ref = ordered[position]
        cipher, plain = folder / "part.bin", folder / "part.pgcopy"
        checkpoint()
        verifier.download(ref["key"], cipher, ref["bytes"])
        _need(
            cipher.stat().st_size == ref["bytes"] and checksum(cipher) == ref["sha256"],
            "Science ciphertext readback differs",
        )
        backup.decrypt(cipher, plain, key, binding["part"]["sha256"], checkpoint=checkpoint)
        _need(plain.stat().st_size == binding["part"]["bytes"], "Science native part size differs")
        columns = next(
            item["columns"] for item in proof["files"] if item["table"] == binding["part"]["table"]
        )
        v3._typed(plain, columns, binding["part"]["rows"], checkpoint)
        v3._drop_cache(cipher)
        v3._drop_cache(plain)
        checkpoint()
    # Root and immutable inventory must still match immediately before durable progress.
    current_session, current_binding = control._authority(
        authority_ledger, source_binding_path, source_binding_sha256, checkpoint
    )
    _need(
        current_session == session and current_binding == source_binding, "Source authority changed"
    )
    control.validate_lease_handoff(lease_frame_path, lease_frame_sha256, source_binding, session)
    _, _, again, after_sha = _inventory(ledger)
    _need(
        after_sha == inventory_sha
        and [item[0][0] for item in again] == [item[0][0] for item in ordered],
        "Science inventory changed during independent readback",
    )
    db = ledger.db
    with db:
        db.execute("""CREATE TABLE IF NOT EXISTS science_v3_reconcile (
            token TEXT PRIMARY KEY, inventory_sha256 TEXT NOT NULL, ciphertext_sha256 TEXT NOT NULL)""")
        old = db.execute(
            "SELECT inventory_sha256,ciphertext_sha256 FROM science_v3_reconcile WHERE token=?",
            (row[0],),
        ).fetchone()
        _need(
            old is None or old == (inventory_sha, ref["sha256"]),
            "Science reconciliation progress differs",
        )
        db.execute(
            "INSERT OR IGNORE INTO science_v3_reconcile VALUES (?,?,?)",
            (row[0], inventory_sha, ref["sha256"]),
        )
        count = db.execute(
            "SELECT count(*) FROM science_v3_reconcile WHERE inventory_sha256=?", (inventory_sha,)
        ).fetchone()[0]
        _need(count <= len(ordered), "Unexpected reconciliation progress")
        if count == len(ordered):
            _need(
                all(
                    db.execute(
                        "SELECT 1 FROM science_v3_reconcile WHERE token=? AND inventory_sha256=? AND ciphertext_sha256=?",
                        (item[0][0], inventory_sha, item[2]["sha256"]),
                    ).fetchone()
                    for item in ordered
                ),
                "Incomplete Science remote readback coverage",
            )
            new_epoch = uuid.uuid4().hex
            db.execute("""CREATE TABLE IF NOT EXISTS science_v3_scope_history (
                epoch TEXT PRIMARY KEY, previous_epoch TEXT NOT NULL, inventory_sha256 TEXT NOT NULL,
                authority_session TEXT NOT NULL, created_at TEXT NOT NULL)""")
            at = datetime.now(UTC).isoformat()
            db.execute(
                "INSERT INTO science_v3_scope_history VALUES (?,?,?,?,?)",
                (new_epoch, epoch[1], inventory_sha, session, at),
            )
            db.execute("UPDATE science_v3_part SET state='verified'")
            db.execute("UPDATE science_v3_whole SET state='verified'")
            db.execute("UPDATE science_v3_scope_epoch SET epoch=? WHERE id=1", (new_epoch,))
            db.execute(
                "UPDATE science_v3_scope SET state='current' WHERE id=1 AND state='needs_reconcile'"
            )
            db.execute("DROP TABLE science_v3_reconcile")
    ledger.publish_read_view()
    return {
        "status": "SCIENCE_SCOPE_CURRENT" if count == len(ordered) else "SCIENCE_RECONCILING",
        "verified_parts": count,
        "total_parts": len(ordered),
        "sql_scope": "HOLD",
        "writer_allowed": False,
    }
