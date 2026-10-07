"""Optional host-wide lease; waiting jobs release their process and memory."""

import fcntl
import json
import os
import stat
import time
from contextlib import contextmanager
from pathlib import Path

from .health import SafetyPause
from .policy import ArchiveBlocked

KINDS = {"copy", "export", "catalog"}


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
            yield
        finally:
            fcntl.flock(body, fcntl.LOCK_UN)
