"""Explicit root-only Science readback, without Source or publisher access.

This proves a part against its historical Source handoff. It cannot accept a
snapshot, enable a writer, authorize deletion, or refresh Source provenance.
"""
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import time
from pathlib import Path

from app.raw_archive import coordination
from app.raw_archive import scientific_parts as parts
from app.raw_archive import scientific_v3 as science
from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.read_only_box import CatalogReadOnlyBox
from app.tiered_archive import backup, storage


def _root_directory(path):
    path = Path(path)
    science._need(path.is_absolute() and path.resolve(strict=True) == path, "Trusted archive path required")
    for item in (path, *path.parents):
        info = item.lstat()
        science._need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
                      "Root-owned archive directory required")
    return path


def _root_file(path, modes):
    path = Path(path)
    _root_directory(path.parent)
    info = path.lstat()
    science._need(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                  and stat.S_IMODE(info.st_mode) in modes, "Root-owned archive file required")
    return info


def _resources_unchecked(workdir, started, machine_sha256):
    """Repeat native archive limits; a caller callback cannot waive these."""
    science._need(sys.platform.startswith("linux") and os.geteuid() == 0
                  and os.getenv("RAW_ARCHIVE_WORKER_ROLE") == "archive"
                  and os.getenv("RAW_ARCHIVE_ENVIRONMENT") == "production"
                  and os.getenv("TIERED_ARCHIVE_ENVIRONMENT") == "production"
                  and re.fullmatch(r"[a-f0-9]{64}", machine_sha256 or "") is not None,
                  "Root production archive authority required")
    need_disabled = ("RAW_ARCHIVE_DELETE_ENABLED", "RETENTION_ENABLED", "DIRECT_RETENTION_ENABLED",
                     "VISUAL_PRUNE_ENABLED", "TIERED_ARCHIVE_DELETE_ENABLED", "TIERED_ARCHIVE_SQL_PRUNE_ENABLED",
                     "TIERED_ARCHIVE_SQL_RETENTION_ACCEPTED")
    science._need(all(os.getenv(name, "false") == "false" for name in need_disabled),
                  "Archive verification cannot enable retention or deletion")
    now = time.monotonic()
    science._need(0 <= now - started < 75, "Archive verification deadline exceeded")
    machine = Path("/etc/machine-id").read_bytes()
    science._need(hashlib.sha256(machine).hexdigest() == machine_sha256, "Archive machine differs")
    membership = Path("/proc/self/cgroup").read_text().strip()
    match = re.fullmatch(r"0::/system.slice/(gm-science-seal-[a-f0-9]{12}\.service)", membership)
    science._need(match is not None and bool(os.getenv("INVOCATION_ID")), "Supervised archive cgroup required")
    group = Path("/sys/fs/cgroup/system.slice") / match.group(1)
    memory = int((group / "memory.max").read_text().strip())
    high = int((group / "memory.high").read_text().strip())
    swap = int((group / "memory.swap.max").read_text().strip())
    quota, period = (int(item) for item in (group / "cpu.max").read_text().split())
    events = dict(line.split() for line in (group / "memory.events").read_text().splitlines())
    available = next(int(line.split()[1]) * 1024 for line in Path("/proc/meminfo").read_text().splitlines()
                     if line.startswith("MemAvailable:"))
    science._need(0 < memory <= 192 * 1024**2 and 0 < high <= 160 * 1024**2 and swap == 0
                  and period > 0 and 0 < quota * 4 <= period
                  and all(events.get(key) == "0" for key in ("high", "max", "oom", "oom_kill"))
                  and available >= 1024 * 1024**2 and os.getloadavg()[0] <= 2
                  and shutil.disk_usage(workdir).free >= 2499805184 + 96 * 1024**2,
                  "Archive verification resource guard failed")


def _resources(workdir, started, machine_sha256):
    try:
        _resources_unchecked(workdir, started, machine_sha256)
    except (OSError, ValueError, StopIteration, OverflowError) as error:
        raise ArchiveBlocked("Archive verification resources unavailable") from error


def checked_file(path, size, digest, checkpoint, modes=(0o600,)):
    """Bind content and inode before and after a bounded archive-local read."""
    before = path.lstat()
    science._need(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
                  and before.st_nlink == 1 and stat.S_IMODE(before.st_mode) in modes
                  and before.st_size == size, "V3 file identity differs")
    fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
    def identity(value):
        return tuple(getattr(value, field) for field in fields)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    h = hashlib.sha256()
    with os.fdopen(fd, "rb") as stream:
        science._need(identity(os.fstat(stream.fileno())) == identity(before), "V3 file replaced")
        while block := stream.read(65536):
            checkpoint()
            h.update(block)
        science._need(identity(os.fstat(stream.fileno())) == identity(before)
                      and identity(path.lstat()) == identity(before) and h.hexdigest() == digest,
                      "V3 file changed or digest differs")
    return identity(before)


def verify(*, metadata, capture_sha256, index_file, index_sha256, position,
           workdir, key, box, ledger, key_prefix, expected_identity,
           expected_epoch, expected_frame_sha256,
           archive_machine_sha256, authority_root, enable_archive_only=False):
    """Verify one prepared part from an independent GET-only account.

    Caller is a root-pinned archive worker holding its actual archive jobs lock.
    The accepted capture handoff is historical, never a fresh Source proof.
    """
    need = science._need
    need(enable_archive_only is True, "Archive-only verification is disabled")
    started = time.monotonic()
    _resources(workdir, started, archive_machine_sha256)
    authority_root = _root_directory(authority_root)
    _root_file(authority_root / "tiered.sqlite3", (0o600,))
    metadata, index_file = _root_directory(metadata), Path(index_file)
    capture_info = _root_file(metadata / "capture.json", (0o400,))
    index_info = _root_file(index_file, (0o400,))
    ledger_file = Path(ledger.root) / "tiered.sqlite3"
    _root_file(ledger_file, (0o600,))
    databases = ledger.db.execute("PRAGMA database_list").fetchall()
    need(len(databases) == 1 and databases[0][1] == "main"
         and Path(databases[0][2]) == ledger_file, "Bound archive ledger required")
    need(type(box) is CatalogReadOnlyBox, "Independent read-only catalog box required")
    need(type(position) is int and 0 <= position < 46, "Invalid V3 part")
    need(type(key) is bytes and len(key) in (16, 24, 32), "Valid AES key required")
    need(not ledger.read_only, "Writable science journal required")
    need(type(expected_identity) is str and box.identity == expected_identity,
         "Read-only destination differs")
    need(re.fullmatch(r"[a-f0-9]{32}", expected_epoch or "") is not None
         and re.fullmatch(r"[a-f0-9]{64}", expected_frame_sha256 or "") is not None,
         "Pinned historical handoff required")
    need(isinstance(key_prefix, str) and key_prefix.endswith("/"), "V3 key prefix required")
    storage.validate_key(key_prefix + "0" * 64 + ".bin")
    workdir = science._directory(workdir)
    _root_directory(workdir)
    verifier = storage.Store(box)
    need(verifier.identity == expected_identity, "Verifier destination differs")

    last_lock_check = [float('-inf')]
    last_resource_check = [float('-inf')]

    def guard(*, force=False):
        now = time.monotonic()
        need(now >= last_lock_check[0], "Archive monotonic clock changed")
        need(not os.path.lexists(authority_root / "PAUSE")
             and not os.path.lexists(authority_root / "control/PAUSE"), "Archive is paused")
        if force or now - last_lock_check[0] >= 0.25:
            coordination.bound_job_lock()
            last_lock_check[0] = now
        if force or now - last_resource_check[0] >= 1:
            _resources(workdir, started, archive_machine_sha256)
            last_resource_check[0] = now

    def epoch():
        actual = science._archive_scope(ledger, capture_sha256, index_sha256)
        need(actual[0] == expected_epoch and actual[5] == expected_frame_sha256,
             "Accepted handoff epoch differs")
        return actual

    guard(force=True)
    capture_generation = checked_file(metadata / "capture.json", capture_info.st_size, capture_sha256, guard, modes=(0o400,))
    index_generation = checked_file(index_file, index_info.st_size, index_sha256, guard, modes=(0o400,))
    fixed_epoch = epoch()
    proof, index = parts.load_index(metadata, capture_sha256, index_file, index_sha256,
                                    checkpoint=guard, verify_files=False)
    need(fixed_epoch[2] == proof["capture_id"] and epoch() == fixed_epoch,
         "Capture handoff changed")
    need(checked_file(metadata / "capture.json", capture_info.st_size, capture_sha256, guard, modes=(0o400,)) == capture_generation
         and checked_file(index_file, index_info.st_size, index_sha256, guard, modes=(0o400,)) == index_generation,
         "Capture metadata generation changed")
    need(position < len(index["parts"]), "Invalid V3 position")
    part = index["parts"][position]
    need(0 < part["bytes"] <= 16 * 1024**2, "V3 part exceeds stage bound")
    binding = {"capture_sha256": capture_sha256, "index_sha256": index_sha256,
               "capture_id": proof["capture_id"], "position": position, "part": part,
               "destination": expected_identity, "key_prefix": key_prefix}
    canonical = science._canonical(binding)
    token = hashlib.sha256(canonical.encode()).hexdigest()
    lockfd = os.open(workdir / "science-v3.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        st = os.fstat(lockfd)
        need(stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid() and st.st_nlink == 1
             and stat.S_IMODE(st.st_mode) == 0o600, "Private V3 lock required")
        fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        guard(force=True)
        need(epoch() == fixed_epoch and not ledger.db.in_transaction, "V3 journal authority changed")
        row = ledger.db.execute("SELECT binding,state,reference FROM science_v3_part WHERE token=?", (token,)).fetchone()
        need(row is not None and row[0] == canonical and row[1] in {"prepared", "verified"},
             "Prepared V3 binding required")
        ref = json.loads(row[2])
        need(type(ref) is dict and set(ref) == {"format", "sha256", "bytes", "plaintext_sha256", "key"}
             and ref["format"] == "bin" and ref["plaintext_sha256"] == part["sha256"]
             and re.fullmatch(r"[a-f0-9]{64}", ref["sha256"]) is not None
             and ref["key"] == key_prefix + ref["sha256"] + ".bin"
             and type(ref["bytes"]) is int and 0 < ref["bytes"] <= storage.MAX_BYTES,
             "Prepared V3 reference differs")
        storage.validate_key(ref["key"])
        stem = workdir / token
        sealed, returned, opened = (stem.with_suffix(s) for s in (".sealed", ".returned", ".opened"))
        required = part["bytes"] * 5 + 2 * 1024**2
        need(required <= parts.MAX_ARCHIVE_WORKSET and required <= 96 * 1024**2, "V3 workset exceeds bound")
        other = 0
        allowed = {stem.with_suffix(s).name for s in (".native", ".sealed", ".returned", ".opened")}
        for entry in workdir.iterdir():
            if entry.name in allowed:
                continue
            st = entry.lstat()
            need(stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid() and st.st_nlink == 1,
                 "Unknown V3 work entry")
            other += st.st_size
            need(required + other <= 96 * 1024**2, "V3 workset exceeds bound")
        storage.disk_reserve(workdir, required)
        if row[1] == "prepared":
            checked_file(sealed, ref["bytes"], ref["sha256"], guard)
        for path, modes in ((returned, (0o600, 0o644)), (opened, (0o600,))):
            science._discard_derivative(path, modes)
        guard(force=True)
        verifier.download(ref["key"], returned, ref["bytes"])
        returned_id = checked_file(returned, ref["bytes"], ref["sha256"], guard, modes=(0o600, 0o644))
        science._drop_cache(returned)
        backup.decrypt(returned, opened, key, part["sha256"], checkpoint=guard)
        need(checked_file(returned, ref["bytes"], ref["sha256"], guard, modes=(0o600, 0o644)) == returned_id,
             "Readback file replaced during decryption")
        opened_id = checked_file(opened, part["bytes"], part["sha256"], guard)
        table = next(x for x in proof["files"] if x["table"] == part["table"])
        science._typed(opened, table["columns"], part["rows"], guard)
        need(checked_file(opened, part["bytes"], part["sha256"], guard) == opened_id,
             "Plaintext file replaced during type verification")
        science._drop_cache(opened)
        guard(force=True)
        need(checked_file(metadata / "capture.json", capture_info.st_size, capture_sha256, guard, modes=(0o400,)) == capture_generation
             and checked_file(index_file, index_info.st_size, index_sha256, guard, modes=(0o400,)) == index_generation,
             "Capture metadata generation changed during verification")
        need(epoch() == fixed_epoch, "V3 epoch changed during verification")
        with ledger.db:
            changed = ledger.db.execute(
                "UPDATE science_v3_part SET state='verified' WHERE token=? AND binding=? AND state=? AND reference=?",
                (token, canonical, row[1], row[2])).rowcount
            need(changed == 1, "V3 journal changed during verification")
            guard(force=True)
            need(epoch() == fixed_epoch, "V3 epoch changed during verification commit")
        for path, modes in ((stem.with_suffix(".native"), (0o400, 0o600)),
                            (sealed, (0o600,)), (returned, (0o600, 0o644)), (opened, (0o600,))):
            science._discard_derivative(path, modes)
        science._sync_dir(workdir)
        return {"stage": "verify", "status": "PART_VERIFIED", "binding": binding,
                "reference": ref, "ciphertext_readback_verified": True, "typed_final_verified": True,
                "native_restore_verified": False, "whole_inventory_authenticated": False,
                "writer_allowed": False, "parent_fk_closure_proven": False, "archive_accepted": False,
                "archive_only_verify": True, "source_access": False,
                "fresh_source_proof": False, "historical_handoff_epoch": expected_epoch}
    except BlockingIOError as exc:
        raise ArchiveBlocked("V3 work directory busy") from exc
    finally:
        os.close(lockfd)
