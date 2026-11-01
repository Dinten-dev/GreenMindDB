"""Optional host-wide lease; waiting jobs release their process and memory."""

import fcntl
import json
import os
import stat
import sys
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from .health import SafetyPause
from .policy import ArchiveBlocked

KINDS = {"copy", "export", "catalog"}
_BOUND_JOB_LOCK = ContextVar("raw_archive_bound_job_lock", default=None)


def bound_job_lock():
    """Return the existing live exclusive lease; never acquire a second lock."""
    authority = _BOUND_JOB_LOCK.get()
    if authority is None:
        raise ArchiveBlocked("Bound archive jobs lock required")
    descriptor, path, process_id, thread_id, _kind = authority
    if process_id != os.getpid() or thread_id != threading.get_ident():
        raise ArchiveBlocked("Archive jobs lock belongs to another execution")
    try:
        held = os.fstat(descriptor)
        current = path.stat(follow_symlinks=False)
    except OSError:
        raise ArchiveBlocked("Bound archive jobs lock unavailable") from None
    if not stat.S_ISREG(current.st_mode) or (held.st_dev, held.st_ino) != (
        current.st_dev,
        current.st_ino,
    ):
        raise ArchiveBlocked("Bound archive jobs lock identity changed")
    if sys.platform.startswith("linux"):
        try:
            entries = Path("/proc/locks").read_text().splitlines()
            device = (os.major(held.st_dev), os.minor(held.st_dev), held.st_ino)
            owned = False
            for entry in entries:
                columns = entry.split()
                if len(columns) < 8 or columns[1:4] != ["FLOCK", "ADVISORY", "WRITE"]:
                    continue
                address = columns[5].split(":")
                if len(address) == 3 and (
                    int(columns[4]) == process_id
                    and (int(address[0], 16), int(address[1], 16), int(address[2])) == device
                    and columns[6:8] == ["0", "EOF"]
                ):
                    owned = True
                    break
        except (OSError, ValueError, OverflowError):
            raise ArchiveBlocked("Archive jobs lock ownership unavailable") from None
        if not owned:
            raise ArchiveBlocked("Archive jobs lock is no longer held by this process")
    return path, held


@contextmanager
def reuse_or_lease(kind):
    """Borrow this execution's live jobs lock or acquire it exactly once."""
    if kind not in KINDS:
        raise ArchiveBlocked("Unknown archive job kind")
    authority = _BOUND_JOB_LOCK.get()
    if authority is None:
        with lease(kind):
            yield
        return
    held_path, _ = bound_job_lock()
    current_dir = directory()
    if current_dir is None or held_path != current_dir / "jobs.lock" or authority[4] != kind:
        raise ArchiveBlocked("Existing archive jobs lock scope differs")
    if kind == "copy" and yield_requested():
        raise SafetyPause("archive_job_yield")
    yield


def directory():
    value = os.getenv("RAW_ARCHIVE_COORDINATION_DIR")
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise ArchiveBlocked("Existing private coordination directory required")
    info = path.stat()
    if info.st_uid not in {0, os.getuid()} or info.st_mode & 0o007:
        raise ArchiveBlocked("Untrusted archive coordination directory")
    return path


def open_private(path, *, readonly=False):
    flags = os.O_RDONLY if readonly else os.O_RDWR | os.O_CREAT
    fd = os.open(path, flags | os.O_NOFOLLOW, 0o660)
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid not in {0, os.getuid()}
        or info.st_mode & 0o007
    ):
        os.close(fd)
        raise ArchiveBlocked("Untrusted archive coordination file")
    return os.fdopen(fd, "r" if readonly else "r+")


def request(path, kind, expires):
    with open_private(path / (kind + ".request")) as body:
        fcntl.flock(body, fcntl.LOCK_EX)
        body.seek(0)
        json.dump({"expires": expires}, body)
        body.truncate()
        body.flush()


def yield_requested(kinds=("export", "catalog")):
    path = directory()
    if path is None:
        return False
    for kind in kinds:
        if kind not in KINDS:
            raise ArchiveBlocked("Unknown archive request kind")
        name = path / (kind + ".request")
        if not name.exists():
            continue
        with open_private(name, readonly=True) as body:
            fcntl.flock(body, fcntl.LOCK_SH)
            raw = body.read(1025)
        try:
            value = json.loads(raw)
            expires = value["expires"]
            if len(raw) > 1024 or type(expires) not in {int, float}:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise ArchiveBlocked("Invalid archive coordination request") from None
        if time.time() < expires <= time.time() + 330:
            return True
    return False


@contextmanager
def lease(kind):
    if kind not in KINDS:
        raise ArchiveBlocked("Unknown archive job kind")
    path = directory()
    if path is None:
        yield
        return
    with open_private(path / "jobs.lock") as body:
        try:
            fcntl.flock(body, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            request(path, kind, time.time() + 300)
            raise SafetyPause("archive_job_busy") from None
        try:
            request(path, kind, 0)
            if kind == "copy" and yield_requested():
                raise SafetyPause("archive_job_yield")
            token = _BOUND_JOB_LOCK.set(
                (body.fileno(), path / "jobs.lock", os.getpid(), threading.get_ident(), kind)
            )
            try:
                yield
            finally:
                _BOUND_JOB_LOCK.reset(token)
        finally:
            fcntl.flock(body, fcntl.LOCK_UN)
