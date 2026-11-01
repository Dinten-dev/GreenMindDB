"""Bounded offline V3 part publication; never authorizes writer or deletion.

Caller supplies an already authenticated private transport context and current
resource/lease checkpoint. This module does not create either authority.
"""

import fcntl
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

from app.tiered_archive import backup, storage

from . import catalog_capture
from . import scientific_parts as parts
from .policy import ArchiveBlocked, RemoteMissing, checksum


def _need(value, message):
    if not value:
        raise ArchiveBlocked(message)


def _directory(path):
    path = Path(path)
    info = path.lstat()
    _need(
        path.is_absolute()
        and not path.is_symlink()
        and stat.S_ISDIR(info.st_mode)
        and info.st_uid == os.geteuid()
        and stat.S_IMODE(info.st_mode) == 0o700,
        "Private V3 work directory required",
    )
    return path


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _archive_scope(ledger, capture_sha256=None, index_sha256=None, capture_id=None):
    exists = ledger.db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='science_v3_scope'"
    ).fetchone()
    _need(
        exists
        and ledger.db.execute("SELECT state FROM science_v3_scope WHERE id=1").fetchone()
        == ("current",),
        "Science V3 journal requires evidence-bound bootstrap or reconciliation",
    )
    _need(
        ledger.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='science_v3_scope_epoch'"
        ).fetchone(),
        "Science V3 scope epoch missing",
    )
    epoch = ledger.db.execute(
        "SELECT epoch,source_session,capture_id,capture_sha256,index_sha256,frame_sha256 FROM science_v3_scope_epoch WHERE id=1"
    ).fetchone()
    _need(
        epoch is not None
        and len(epoch) == 6
        and all(
            isinstance(epoch[i], str) and re.fullmatch("[a-f0-9]{32}", epoch[i]) for i in (0, 1, 2)
        )
        and all(
            isinstance(epoch[i], str) and re.fullmatch("[a-f0-9]{64}", epoch[i]) for i in (3, 4, 5)
        )
        and (capture_sha256 is None or epoch[3] == capture_sha256)
        and (index_sha256 is None or epoch[4] == index_sha256)
        and (capture_id is None or epoch[2] == capture_id),
        "Science V3 epoch or capture binding differs",
    )
    return epoch


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _discard_derivative(path, modes):
    """Discard only this locked job's private, reproducible local derivative."""
    if os.path.lexists(path):
        info = path.lstat()
        _need(
            stat.S_ISREG(info.st_mode)
            and info.st_uid == os.geteuid()
            and info.st_nlink == 1
            and stat.S_IMODE(info.st_mode) in modes,
            "Unsafe interrupted V3 work file",
        )
        path.unlink()


def _drop_cache(path):
    """Release only this job's durable local derivative from Linux page cache."""
    if not sys.platform.startswith("linux"):
        return
    _need(
        hasattr(os, "posix_fadvise") and hasattr(os, "POSIX_FADV_DONTNEED"),
        "Linux V3 cache release unavailable",
    )
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        _need(
            stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and info.st_nlink == 1,
            "Private V3 cache-release file required",
        )
        os.fsync(fd)
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    except OSError as exc:
        raise ArchiveBlocked("Linux V3 cache release failed") from exc
    finally:
        os.close(fd)


def _typed(path, columns, count, checkpoint):
    observed = 0
    try:
        for row in catalog_capture.rows(path, [c["type"] for c in columns], native=True):
            checkpoint()
            _need(row[0] is not None, "V3 part has NULL primary key")
            observed += 1
    except ArchiveBlocked:
        raise
    except (ValueError, TypeError, OverflowError, UnicodeError) as exc:
        raise ArchiveBlocked("V3 part native type invalid") from exc
    _need(observed == count, "V3 typed part count differs")


def _copy_received(source, target, part, columns, checkpoint):
    """Freeze exactly the privately transported part while checking framing and digest."""
    source = Path(source)
    before = source.lstat()
    _need(
        source.is_absolute()
        and stat.S_ISREG(before.st_mode)
        and before.st_uid == os.geteuid()
        and before.st_nlink == 1
        and stat.S_IMODE(before.st_mode) in (0o400, 0o600)
        and before.st_size == part["bytes"],
        "Private received V3 part required",
    )
    from .scientific_capture import FrameCounter

    counter = FrameCounter(len(columns))
    digest = hashlib.sha256()
    in_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        out_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except BaseException:
        os.close(in_fd)
        raise
    with os.fdopen(in_fd, "rb") as src, os.fdopen(out_fd, "wb") as dest:
        actual = os.fstat(src.fileno())
        _need(
            (actual.st_dev, actual.st_ino) == (before.st_dev, before.st_ino),
            "Received V3 part generation differs",
        )
        while block := src.read(65536):
            checkpoint()
            counter.feed(block)
            digest.update(block)
            dest.write(block)
        after = os.fstat(src.fileno())
        _need(
            (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            == (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            and digest.hexdigest() == part["sha256"]
            and counter.finish() == part["rows"],
            "Received V3 part differs from pinned inventory",
        )
        dest.flush()
        os.fsync(dest.fileno())
        os.fchmod(dest.fileno(), 0o400)
        os.fsync(dest.fileno())


def _stage_authority(stage):
    """Phased jobs must retain the real archive lock and source lease."""
    if stage != "complete":
        from .coordination import bound_job_lock
        from .remote_lease import require_bound_source_lease
        bound_job_lock()
        require_bound_source_lease()


def publish_part(
    source,
    capture_sha256,
    index_file,
    index_sha256,
    position,
    workdir,
    key,
    publisher,
    verifier,
    ledger,
    key_prefix,
    *,
    checkpoint,
    received_part=None,
    stage="complete",
):
    """Seal and reconcile one part using existing AES, Store and Ledger primitives.

    If received_part is set, source contains only immutable capture.json and the
    securely transported <=32 MiB part is verified against the exact inventory.
    A separately reviewed private bridge is required. Never treats this receipt as a
    native restore or a writer approval. Resume uses the exact frozen ciphertext.
    """
    _need(type(stage) is str and stage in {"prepare", "transfer", "verify", "complete"}, "Invalid V3 publication stage")
    _stage_authority(stage)
    _need(callable(checkpoint) and publisher is not verifier, "Separate verifier required")
    _need(type(key) is bytes and len(key) in (16, 24, 32), "Valid AES key required")
    _need(
        not ledger.read_only and publisher.identity == verifier.identity,
        "Matching destination and writable archive journal required",
    )
    _archive_scope(ledger, capture_sha256, index_sha256)
    workdir = _directory(workdir)
    _need(isinstance(key_prefix, str) and key_prefix.endswith("/"), "V3 storage prefix required")
    # Reuses the exact existing path policy, including namespace/date/organization.
    storage.validate_key(key_prefix + "0" * 64 + ".bin")
    lockfd = os.open(workdir / "science-v3.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        checkpoint()
        proof, index = parts.load_index(
            source,
            capture_sha256,
            index_file,
            index_sha256,
            checkpoint=checkpoint,
            verify_files=received_part is None,
        )
        epoch = _archive_scope(ledger, capture_sha256, index_sha256, proof["capture_id"])
        _need(type(position) is int and 0 <= position < len(index["parts"]), "Invalid V3 part")
        part = index["parts"][position]
        if stage != "complete":
            _need(part["bytes"] <= 16 * 1024**2, "Staged V3 part exceeds 16 MiB bound")
        binding = {
            "capture_sha256": capture_sha256,
            "index_sha256": index_sha256,
            "capture_id": proof["capture_id"],
            "position": position,
            "part": part,
            "destination": publisher.identity,
            "key_prefix": key_prefix,
        }
        token = hashlib.sha256(_canonical(binding).encode()).hexdigest()
        db = ledger.db
        _need(not db.in_transaction, "Dedicated idle V3 journal connection required")
        db.execute("""CREATE TABLE IF NOT EXISTS science_v3_part (
            token TEXT PRIMARY KEY, binding TEXT NOT NULL, state TEXT NOT NULL,
            reference TEXT NOT NULL)""")
        db.commit()
        previous = db.execute(
            "SELECT binding,state,reference FROM science_v3_part WHERE token=?", (token,)
        ).fetchone()
        stem = workdir / token
        plain = stem.with_suffix(".native")
        sealed = stem.with_suffix(".sealed")
        returned = stem.with_suffix(".returned")
        opened = stem.with_suffix(".opened")
        # Include the received part and Store.publish's own readback temporary.
        required = part["bytes"] * (6 if stage == "complete" else 5) + 2 * 1024 * 1024
        _need(required <= parts.MAX_ARCHIVE_WORKSET, "V3 workset exceeds 256 MiB")
        if stage != "complete":
            # Do not erase unrelated work to make room. Account for every other
            # private regular file; unknown directories/symlinks block safely.
            current = {plain.name, sealed.name, returned.name, opened.name}
            other_bytes = 0
            for entry in workdir.iterdir():
                if entry.name in current:
                    continue
                info = entry.lstat()
                _need(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and info.st_nlink == 1,
                      "Unknown V3 work directory entry")
                other_bytes += info.st_size
                _need(required + other_bytes <= 96 * 1024**2, "Staged V3 workset exceeds 96 MiB")
            _need(required + other_bytes <= 96 * 1024**2, "Staged V3 workset exceeds 96 MiB")
        storage.disk_reserve(workdir, required)
        if previous is None:
            _need(stage in {"prepare", "complete"}, "Prepared V3 journal required before this stage")
            # No upload can occur before a prepared journal entry exists.
            for path, modes in (
                (plain, (0o400, 0o600)),
                (sealed, (0o600,)),
                (returned, (0o600, 0o644)),
                (opened, (0o600,)),
            ):
                _discard_derivative(path, modes)
            table = next(x for x in proof["files"] if x["table"] == part["table"])
            if received_part is None:
                parts.materialize_part(
                    source,
                    capture_sha256,
                    index_file,
                    index_sha256,
                    position,
                    plain,
                    checkpoint=checkpoint,
                )
            else:
                _copy_received(received_part, plain, part, table["columns"], checkpoint)
            _typed(plain, table["columns"], part["rows"], checkpoint)
            _drop_cache(plain)
            meta = backup.encrypt(plain, sealed, key, checkpoint=checkpoint)
            _drop_cache(plain)
            _drop_cache(sealed)
            _need(meta["plaintext_sha256"] == part["sha256"], "V3 source part changed")
            _sync_dir(workdir)
            ref = {**meta, "key": key_prefix + meta["sha256"] + ".bin"}
            storage.validate_key(ref["key"])
            # Prepared is committed before any upload, including uncertain outcomes.
            checkpoint()
            _stage_authority(stage)
            _need(_archive_scope(ledger, capture_sha256, index_sha256, proof["capture_id"]) == epoch,
                  "V3 scope changed before prepare commit")
            with db:
                db.execute(
                    "INSERT INTO science_v3_part VALUES (?,?,?,?)",
                    (token, _canonical(binding), "prepared", _canonical(ref)),
                )
        else:
            _need(
                previous[0] == _canonical(binding) and previous[1] in {"prepared", "verified"},
                "V3 journal binding differs",
            )
            ref = json.loads(previous[2])
            _need(
                set(ref) == {"format", "sha256", "bytes", "plaintext_sha256", "key"}
                and ref["format"] == "bin"
                and ref["plaintext_sha256"] == part["sha256"]
                and ref["key"] == key_prefix + ref["sha256"] + ".bin"
                and re.fullmatch(r"[a-f0-9]{64}", ref["sha256"])
                and type(ref["bytes"]) is int
                and 0 < ref["bytes"] <= storage.MAX_BYTES,
                "V3 journal reference differs",
            )
            storage.validate_key(ref["key"])
        if stage != "complete":
            _need(previous is None or previous[1] == "prepared", "Staged V3 part must remain prepared")
            try:
                frozen_info = sealed.lstat()
            except FileNotFoundError:
                raise ArchiveBlocked("Frozen ciphertext missing or changed") from None
            _need(stat.S_ISREG(frozen_info.st_mode) and frozen_info.st_uid == os.geteuid()
                  and frozen_info.st_nlink == 1 and stat.S_IMODE(frozen_info.st_mode) == 0o600
                  and frozen_info.st_size == ref["bytes"] and checksum(sealed) == ref["sha256"],
                  "Frozen ciphertext missing or changed")
            _discard_derivative(plain, (0o400, 0o600))
            _sync_dir(workdir)
        if stage == "transfer":
            # A different key must never cause publication of the frozen object.
            _discard_derivative(opened, (0o600,))
            backup.decrypt(sealed, opened, key, part["sha256"], checkpoint=checkpoint)
            _drop_cache(opened)
            _discard_derivative(opened, (0o600,))
            _sync_dir(workdir)
        if stage == "prepare":
            # A resumed prepared journal must still authenticate its frozen AES key
            # and pass the first complete native type check; no network is used.
            if previous is not None:
                _discard_derivative(opened, (0o600,))
                backup.decrypt(sealed, opened, key, part["sha256"], checkpoint=checkpoint)
                table = next(x for x in proof["files"] if x["table"] == part["table"])
                _typed(opened, table["columns"], part["rows"], checkpoint)
                _drop_cache(opened)
                _discard_derivative(opened, (0o600,))
                _sync_dir(workdir)
            checkpoint()
            _stage_authority(stage)
            _need(_archive_scope(ledger, capture_sha256, index_sha256, proof["capture_id"]) == epoch,
                  "V3 scope changed during prepare")
            return {"stage": "prepare", "status": "PREPARED", "binding": binding, "reference": ref,
                    "ciphertext_readback_verified": False, "typed_final_verified": False,
                    "archive_accepted": False, "native_restore_verified": False,
                    "whole_inventory_authenticated": False, "writer_allowed": False,
                    "parent_fk_closure_proven": False}
        checkpoint()
        # Always use read-only verifier to reconcile the exact content-addressed key.
        _discard_derivative(returned, (0o600, 0o644))
        _discard_derivative(opened, (0o600,))
        try:
            verifier.download(ref["key"], returned, ref["bytes"])
        except RemoteMissing:
            _need(stage != "verify", "Verify stage cannot publish missing ciphertext")
            _need(previous is None or previous[1] == "prepared", "Verified V3 object missing")
            _need(
                sealed.is_file()
                and not sealed.is_symlink()
                and sealed.stat().st_size == ref["bytes"]
                and checksum(sealed) == ref["sha256"],
                "Frozen ciphertext missing or changed",
            )
            checkpoint()
            _stage_authority(stage)
            _need(_archive_scope(ledger, capture_sha256, index_sha256, proof["capture_id"]) == epoch,
                  "V3 scope changed before publication")
            publisher.publish(ref["key"], sealed)
            _drop_cache(sealed)
            checkpoint()
            verifier.download(ref["key"], returned, ref["bytes"])
        _need(
            returned.stat().st_size == ref["bytes"] and checksum(returned) == ref["sha256"],
            "Independent V3 ciphertext readback differs",
        )
        _drop_cache(returned)
        backup.decrypt(returned, opened, key, part["sha256"], checkpoint=checkpoint)
        _drop_cache(returned)
        if stage == "transfer":
            _drop_cache(opened)
            _discard_derivative(returned, (0o600, 0o644))
            _discard_derivative(opened, (0o600,))
            _sync_dir(workdir)
            checkpoint()
            _stage_authority(stage)
            _need(_archive_scope(ledger, capture_sha256, index_sha256, proof["capture_id"]) == epoch,
                  "V3 scope changed during transfer")
            _need(db.execute("SELECT binding,state,reference FROM science_v3_part WHERE token=?", (token,)).fetchone()
                  == (_canonical(binding), "prepared", _canonical(ref)), "Prepared V3 journal changed")
            return {"stage": "transfer", "status": "CIPHERTEXT_READBACK_VERIFIED", "binding": binding,
                    "reference": ref, "ciphertext_readback_verified": True, "typed_final_verified": False,
                    "archive_accepted": False, "native_restore_verified": False,
                    "whole_inventory_authenticated": False, "writer_allowed": False,
                    "parent_fk_closure_proven": False}
        table = next(x for x in proof["files"] if x["table"] == part["table"])
        _typed(opened, table["columns"], part["rows"], checkpoint)
        _drop_cache(opened)
        checkpoint()
        _stage_authority(stage)
        _need(_archive_scope(ledger, capture_sha256, index_sha256, proof["capture_id"]) == epoch,
              "V3 scope changed before verification commit")
        with db:
            expected_state = previous[1] if previous is not None else "prepared"
            changed = db.execute("UPDATE science_v3_part SET state='verified' WHERE token=? AND binding=? AND state=? AND reference=?",
                                 (token, _canonical(binding), expected_state, _canonical(ref))).rowcount
            _need(changed == 1, "V3 journal changed before verification commit")
            checkpoint()
            _stage_authority(stage)
            _need(_archive_scope(ledger, capture_sha256, index_sha256, proof["capture_id"]) == epoch,
                  "V3 scope changed during verification commit")
        # Remove only files created by this job and only after durable readback.
        for path in (plain, sealed, returned, opened):
            if path.exists():
                _need(path.is_file() and not path.is_symlink(), "Unsafe V3 work file")
                path.unlink()
        _sync_dir(workdir)
        result = {
            "binding": binding,
            "reference": ref,
            "ciphertext_readback_verified": True,
            "native_restore_verified": False,
            "whole_inventory_authenticated": False,
            "writer_allowed": False,
            "parent_fk_closure_proven": False,
        }
        if stage == "verify":
            result.update(stage="verify", status="PART_VERIFIED", typed_final_verified=True, archive_accepted=False)
        return result
    except BlockingIOError as exc:
        raise ArchiveBlocked("V3 work directory busy") from exc
    finally:
        os.close(lockfd)


def writer_gate(*_args, **_kwargs):
    raise ArchiveBlocked("V3 whole-inventory, native restore and Direct baseline are not accepted")


def publish_whole(
    source,
    capture_sha256,
    index_file,
    index_sha256,
    workdir,
    key,
    publisher,
    verifier,
    ledger,
    key_prefix,
    *,
    checkpoint,
):
    """Publish authenticated whole inventory only after every ordered part readback.

    The input capture/index are revalidated, and journal records are bound to the
    exact ordered inventory and destination. This is not a native restore proof.
    """
    _need(callable(checkpoint) and publisher is not verifier, "Separate verifier required")
    _need(type(key) is bytes and len(key) in (16, 24, 32), "Valid AES key required")
    _need(
        not ledger.read_only and publisher.identity == verifier.identity,
        "Matching destination and writable archive journal required",
    )
    _archive_scope(ledger, capture_sha256, index_sha256)
    workdir = _directory(workdir)
    _need(isinstance(key_prefix, str) and key_prefix.endswith("/"), "V3 storage prefix required")
    storage.validate_key(key_prefix + "0" * 64 + ".bin")
    lockfd = os.open(workdir / "science-v3.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        checkpoint()
        proof, index = parts.load_index(
            source,
            capture_sha256,
            index_file,
            index_sha256,
            checkpoint=checkpoint,
            verify_files=False,
        )
        _archive_scope(ledger, capture_sha256, index_sha256, proof["capture_id"])
        db = ledger.db
        _need(not db.in_transaction, "Dedicated idle V3 journal connection required")
        db.execute("""CREATE TABLE IF NOT EXISTS science_v3_part (
            token TEXT PRIMARY KEY, binding TEXT NOT NULL, state TEXT NOT NULL,
            reference TEXT NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS science_v3_whole (
            token TEXT PRIMARY KEY, body_sha256 TEXT NOT NULL, state TEXT NOT NULL,
            reference TEXT NOT NULL)""")
        db.commit()
        references = []
        for position, part in enumerate(index["parts"]):
            checkpoint()
            binding = {
                "capture_sha256": capture_sha256,
                "index_sha256": index_sha256,
                "capture_id": proof["capture_id"],
                "position": position,
                "part": part,
                "destination": publisher.identity,
                "key_prefix": key_prefix,
            }
            token = hashlib.sha256(_canonical(binding).encode()).hexdigest()
            record = db.execute(
                "SELECT binding,state,reference FROM science_v3_part WHERE token=?", (token,)
            ).fetchone()
            _need(
                record is not None and record[0] == _canonical(binding) and record[1] == "verified",
                "Every ordered V3 part requires its bound readback",
            )
            ref = json.loads(record[2])
            _need(
                type(ref) is dict
                and set(ref) == {"format", "sha256", "bytes", "plaintext_sha256", "key"}
                and ref["format"] == "bin"
                and ref["plaintext_sha256"] == part["sha256"]
                and isinstance(ref["sha256"], str)
                and re.fullmatch(r"[a-f0-9]{64}", ref["sha256"])
                and ref["key"] == key_prefix + ref["sha256"] + ".bin"
                and type(ref["bytes"]) is int
                and 0 < ref["bytes"] <= storage.MAX_BYTES,
                "V3 part journal reference differs",
            )
            storage.validate_key(ref["key"])
            references.append(ref)
        _need(len({x["key"] for x in references}) == len(references), "Duplicate V3 part objects")
        body = {
            "format": "greenmind.science-whole.v3",
            "capture": proof,
            "capture_sha256": capture_sha256,
            "index": index,
            "index_sha256": index_sha256,
            "destination": publisher.identity,
            "references": references,
            "native_restore_verified": False,
            "parent_fk_closure_proven": False,
            "cross_database_atomic": False,
        }
        raw = (_canonical(body) + "\n").encode()
        _need(len(raw) <= 16 * 1024**2, "V3 whole manifest exceeds budget")
        whole_sha = hashlib.sha256(raw).hexdigest()
        token = hashlib.sha256(("whole:" + whole_sha).encode()).hexdigest()
        previous = db.execute(
            "SELECT body_sha256,state,reference FROM science_v3_whole WHERE token=?", (token,)
        ).fetchone()
        stem = workdir / token
        plain, sealed, returned, opened = (
            stem.with_suffix(s) for s in (".whole", ".sealed", ".returned", ".opened")
        )
        # Include Store.publish's additional independent-readback temporary.
        required = len(raw) * 6 + 2 * 1024 * 1024
        _need(required <= parts.MAX_ARCHIVE_WORKSET, "V3 whole workset exceeds budget")
        storage.disk_reserve(workdir, required)
        if previous is None:
            for path, modes in (
                (plain, (0o600,)),
                (sealed, (0o600,)),
                (returned, (0o600, 0o644)),
                (opened, (0o600,)),
            ):
                _discard_derivative(path, modes)
            fd = os.open(plain, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
            meta = backup.encrypt(plain, sealed, key, checkpoint=checkpoint)
            _drop_cache(plain)
            _drop_cache(sealed)
            _need(meta["plaintext_sha256"] == whole_sha, "V3 whole manifest changed")
            _sync_dir(workdir)
            ref = {**meta, "key": key_prefix + meta["sha256"] + ".bin"}
            storage.validate_key(ref["key"])
            with db:
                db.execute(
                    "INSERT INTO science_v3_whole VALUES (?,?,?,?)",
                    (token, whole_sha, "prepared", _canonical(ref)),
                )
        else:
            _need(
                previous[0] == whole_sha and previous[1] in {"prepared", "verified"},
                "V3 whole journal binding differs",
            )
            ref = json.loads(previous[2])
            _need(
                type(ref) is dict
                and set(ref) == {"format", "sha256", "bytes", "plaintext_sha256", "key"}
                and ref["format"] == "bin"
                and ref["plaintext_sha256"] == whole_sha
                and isinstance(ref["sha256"], str)
                and re.fullmatch(r"[a-f0-9]{64}", ref["sha256"])
                and ref["key"] == key_prefix + ref["sha256"] + ".bin"
                and type(ref["bytes"]) is int
                and 0 < ref["bytes"] <= storage.MAX_BYTES,
                "V3 whole reference differs",
            )
            storage.validate_key(ref["key"])
        _discard_derivative(returned, (0o600, 0o644))
        _discard_derivative(opened, (0o600,))
        checkpoint()
        try:
            verifier.download(ref["key"], returned, ref["bytes"])
        except RemoteMissing:
            _need(previous is None or previous[1] == "prepared", "Verified V3 whole missing")
            _need(
                sealed.is_file()
                and not sealed.is_symlink()
                and sealed.stat().st_size == ref["bytes"]
                and checksum(sealed) == ref["sha256"],
                "Frozen V3 whole ciphertext missing or changed",
            )
            checkpoint()
            publisher.publish(ref["key"], sealed)
            _drop_cache(sealed)
            checkpoint()
            verifier.download(ref["key"], returned, ref["bytes"])
        _need(
            returned.stat().st_size == ref["bytes"] and checksum(returned) == ref["sha256"],
            "V3 whole ciphertext readback differs",
        )
        _drop_cache(returned)
        backup.decrypt(returned, opened, key, whole_sha, checkpoint=checkpoint)
        _drop_cache(returned)
        _need(opened.read_bytes() == raw, "V3 authenticated whole content differs")
        _drop_cache(opened)
        checkpoint()
        with db:
            db.execute("UPDATE science_v3_whole SET state='verified' WHERE token=?", (token,))
        for path in (plain, sealed, returned, opened):
            if path.exists():
                _need(path.is_file() and not path.is_symlink(), "Unsafe V3 whole work file")
                path.unlink()
        _sync_dir(workdir)
        return {
            "reference": ref,
            "whole_sha256": whole_sha,
            "capture_id": proof["capture_id"],
            "part_count": len(references),
            "whole_inventory_authenticated": True,
            "native_restore_verified": False,
            "writer_allowed": False,
            "parent_fk_closure_proven": False,
        }
    except BlockingIOError as exc:
        raise ArchiveBlocked("V3 work directory busy") from exc
    finally:
        os.close(lockfd)


def direct_baseline_context(absence, absence_sha256, provider_sha256, historical_originals_sha256):
    """Bind opaque existing Direct context without claiming any scientific rows.

    Caller must authenticate both referenced artifacts and prove their scope and
    consistency separately. This is a binding record, not a backup acceptance.
    """
    for value in (absence_sha256, provider_sha256, historical_originals_sha256):
        _need(
            isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value),
            "Pinned Direct context digests required",
        )
    _need(
        type(absence) is dict
        and set(absence)
        == {
            "format",
            "capture_id",
            "source",
            "identity",
            "snapshot_sha256",
            "tables",
            "complete",
            "cross_database_atomic",
            "prior_direct_data_captured",
        }
        and absence["format"] == "greenmind.science-direct-absence.v3"
        and absence["source"] == "direct"
        and absence["complete"] is True
        and absence["cross_database_atomic"] is False
        and absence["prior_direct_data_captured"] is False
        and absence["tables"] == {"direct_feature_result": "absent", "direct_feature_job": "absent"}
        and isinstance(absence["capture_id"], str)
        and re.fullmatch(r"[a-f0-9]{32}", absence["capture_id"])
        and isinstance(absence["snapshot_sha256"], str)
        and re.fullmatch(r"[a-f0-9]{64}", absence["snapshot_sha256"])
        and hashlib.sha256((_canonical(absence) + "\n").encode()).hexdigest() == absence_sha256,
        "Exact Direct absence evidence required",
    )
    ident = absence["identity"]
    _need(
        type(ident) is dict
        and ident.get("database") == "greenmind_direct_production"
        and ident.get("database_oid") == 301614
        and ident.get("role") == "greenmind_archive_reader_33fe395"
        and ident.get("session_role") == ident["role"]
        and ident.get("role_oid") == ident.get("session_oid") == 499352
        and ident.get("role_unprivileged") is True
        and ident.get("server_version_num") == 150010
        and ident.get("transaction_read_only") == "on"
        and ident.get("in_recovery") is False
        and ident.get("server_address") == "172.28.20.5/32"
        and ident.get("server_port") == 5432
        and ident.get("postmaster_start") == "2026-08-31 16:13:00.505241+00",
        "Direct absence source identity differs",
    )
    return {
        "format": "greenmind.science-direct-context.v3",
        "absence_sha256": absence_sha256,
        "absence_capture_id": absence["capture_id"],
        "tables": absence["tables"],
        "provider_backup_sha256": provider_sha256,
        "historical_originals_context_sha256": historical_originals_sha256,
        "artifact_scope_and_readback_verified": False,
        "prior_direct_data_captured": False,
        "scientific_preservation_proven": False,
        "cross_database_atomic": False,
        "writer_allowed": False,
    }
