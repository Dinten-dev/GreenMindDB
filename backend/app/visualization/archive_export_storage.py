"""Private export copies only: bounded storage, process locks and reaping.

No object-store client, original recording path or archive deletion authority.
API and workers must use the same directory/striped job locks.
"""

import fcntl
import hashlib
import json
import os
import re
import stat
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from app.visualization.archive_jobs import EXPIRES, connect

UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
COPY = re.compile(
    rf"({UUID})-(?:[0-9]{{3,}}(?:-[0-9a-f]{{32}})?\.(?:zip|part)|[0-9a-f]{{32}}\.raw-part)\Z"
)
METADATA = {
    "jobs.sqlite3",
    "jobs.sqlite3-wal",
    "jobs.sqlite3-shm",
    "jobs.sqlite3-journal",
    "restoration.json",
    "merge-restoration.json",
}
GLOBAL_BYTES = 8 * 1024**3
USER_BYTES = 4 * 1024**3
ORPHAN_SECONDS = 300


class ExportStorageError(RuntimeError):
    pass


class ExportQuota(ExportStorageError):
    pass


def private_directory(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise ExportStorageError("Private canonical export directory required")
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    info = os.fstat(fd)
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        os.close(fd)
        raise ExportStorageError("Private owned export directory required")
    return fd


def safe_job(identity):
    if not isinstance(identity, str) or str(uuid.UUID(identity)) != identity:
        raise ExportStorageError("Invalid export identity")
    return identity


def part_names(row):
    names = json.loads(row["parts"])
    if not isinstance(names, list) or any(
        not isinstance(n, str)
        or not COPY.fullmatch(n)
        or not n.startswith(row["id"] + "-")
        or not n.endswith(".zip")
        for n in names
    ):
        raise ExportStorageError("Invalid retained export parts")
    return set(names)


class ExportStore:
    def __init__(self, path):
        self.path = Path(path)
        for key in ("RAW_ARCHIVE_STATE_DIR", "DIRECT_ARTIFACT_ROOT", "VISUAL_ARCHIVE_DIR"):
            value = os.getenv(key)
            if value:
                protected = Path(value).resolve()
                if self.path.is_relative_to(protected) or protected.is_relative_to(self.path):
                    raise ExportStorageError("Export directory overlaps protected storage")
        self.fd = private_directory(self.path)
        self.depth = 0
        self.locks = None
        self.scratch = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        for name in ("scratch", "locks", "fd"):
            value = getattr(self, name)
            if value is not None:
                os.close(value)
                setattr(self, name, None)

    @contextmanager
    def locked(self):
        if not self.depth:
            fcntl.flock(self.fd, fcntl.LOCK_EX)
        self.depth += 1
        try:
            current = self.path.stat(follow_symlinks=False)
            held = os.fstat(self.fd)
            if (current.st_dev, current.st_ino) != (held.st_dev, held.st_ino):
                raise ExportStorageError("Export directory changed")
            yield self
        finally:
            self.depth -= 1
            if not self.depth:
                fcntl.flock(self.fd, fcntl.LOCK_UN)

    def directory(self, name):
        try:
            os.mkdir(name, 0o700, dir_fd=self.fd)
        except FileExistsError:
            pass
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.fd)
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            os.close(fd)
            raise ExportStorageError("Unsafe export subdirectory")
        return fd

    def acquire_job(self, identity, *, shared=False):
        safe_job(identity)
        if self.locks is None:
            self.locks = self.directory(".locks")
        # Fixed-size stripes cannot accumulate one permanent lock per old job.
        stripe = int(hashlib.sha256(identity.encode()).hexdigest()[:4], 16) % 64
        fd = os.open(
            f"{stripe:02d}", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=self.locks
        )
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & 0o077
        ):
            os.close(fd)
            raise ExportStorageError("Unsafe export lock")
        try:
            fcntl.flock(fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            os.close(fd)
            return None

    def scratch_path(self, identity):
        safe_job(identity)
        if self.scratch is None:
            self.scratch = self.directory(".scratch")
        try:
            os.mkdir(identity, 0o700, dir_fd=self.scratch)
        except FileExistsError:
            pass
        fd = os.open(identity, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.scratch)
        info = os.fstat(fd)
        os.close(fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ExportStorageError("Unsafe export scratch")
        return self.path / ".scratch" / identity

    def inventory(self):
        """Count all retained entries, including unknown files; never follow links."""
        usage = {}
        entries = 0

        def visit(fd, owner=None, depth=0):
            nonlocal entries
            if depth > 6:
                raise ExportStorageError("Export inventory depth exceeded")
            for name in os.listdir(fd):
                entries += 1
                if entries > 50000:
                    raise ExportStorageError("Export inventory count exceeded")
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if info.st_uid != os.getuid() or stat.S_ISLNK(info.st_mode):
                    raise ExportStorageError("Unsafe export inventory entry")
                match = COPY.fullmatch(name) if depth == 0 else None
                identity = match[1] if match else owner
                if depth == 1 and owner == "scratch":
                    identity = name if re.fullmatch(UUID, name) else None
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        visit(
                            child,
                            "scratch" if depth == 0 and name == ".scratch" else identity,
                            depth + 1,
                        )
                    finally:
                        os.close(child)
                elif stat.S_ISREG(info.st_mode):
                    usage[identity] = usage.get(identity, 0) + max(
                        info.st_size, getattr(info, "st_blocks", 0) * 512
                    )
                else:
                    raise ExportStorageError("Unsafe export inventory entry")

        visit(self.fd)
        return usage

    def budget(self, db, *, incoming=None, growing=None, additional=0):
        usage = self.inventory()
        users = {}
        for row in db.execute("SELECT * FROM jobs"):
            actual = usage.pop(row["id"], 0)
            if growing == row["id"]:
                actual += additional
            amount = (
                max(actual, reservation(json.loads(row["items"])))
                if row["status"] in {"queued", "working"}
                else actual
            )
            users[row["user_id"]] = users.get(row["user_id"], 0) + amount
        if incoming:
            user, items = incoming
            users[user] = users.get(user, 0) + reservation(items)
        if sum(users.values()) + sum(usage.values()) > GLOBAL_BYTES or any(
            v > USER_BYTES for v in users.values()
        ):
            raise ExportQuota("Retained export storage limit reached")

    def unlink(self, name):
        if not COPY.fullmatch(name):
            raise ExportStorageError("Only generated export copies may be removed")
        try:
            info = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        except FileNotFoundError:
            return 0
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ExportStorageError("Unsafe generated export copy")
        os.unlink(name, dir_fd=self.fd)
        return 1

    def clear_scratch(self, identity):
        safe_job(identity)
        try:
            parent = os.open(
                ".scratch", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.fd
            )
        except FileNotFoundError:
            return

        def clear(fd, depth=0):
            if depth > 5:
                raise ExportStorageError("Unsafe export scratch depth")
            for name in os.listdir(fd):
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if info.st_uid != os.getuid():
                    raise ExportStorageError("Unsafe export scratch owner")
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        clear(child, depth + 1)
                    finally:
                        os.close(child)
                    os.rmdir(name, dir_fd=fd)
                elif stat.S_ISREG(info.st_mode):
                    os.unlink(name, dir_fd=fd)
                else:
                    raise ExportStorageError("Unsafe export scratch entry")

        try:
            try:
                fd = os.open(identity, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            except FileNotFoundError:
                return
            try:
                clear(fd)
            finally:
                os.close(fd)
            os.rmdir(identity, dir_fd=parent)
        finally:
            os.close(parent)

    def clean_job(self, row, *, keep=()):
        identity = safe_job(row["id"])
        deleted = 0
        for name in os.listdir(self.fd):
            match = COPY.fullmatch(name)
            if match and match[1] == identity and name not in keep:
                deleted += self.unlink(name)
        self.clear_scratch(identity)
        return deleted

    def reap(self, db, *, now=None):
        now = time.time() if now is None else now
        deleted = 0
        rows = {row["id"]: row for row in db.execute("SELECT * FROM jobs")}
        identities = set(rows)
        for name in os.listdir(self.fd):
            match = COPY.fullmatch(name)
            if (
                match
                and os.stat(name, dir_fd=self.fd, follow_symlinks=False).st_mtime
                <= now - ORPHAN_SECONDS
            ):
                identities.add(match[1])
        try:
            scratch = os.open(
                ".scratch", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.fd
            )
        except FileNotFoundError:
            scratch = None
        if scratch is not None:
            try:
                for name in os.listdir(scratch):
                    if (
                        re.fullmatch(UUID, name)
                        and os.stat(name, dir_fd=scratch, follow_symlinks=False).st_mtime
                        <= now - ORPHAN_SECONDS
                    ):
                        identities.add(name)
            finally:
                os.close(scratch)
        for identity in sorted(identities):
            safe_job(identity)
            lock = self.acquire_job(identity)
            if lock is None:
                continue  # Active worker or streaming download; no age override.
            try:
                row = rows.get(identity)
                expired = row is not None and row["created"] + EXPIRES <= now
                if row is None or expired or row["status"] in {"failed", "cancelled", "expired"}:
                    deleted += self.clean_job(row or {"id": identity})
                    if row:
                        db.execute(
                            "UPDATE jobs SET status=?,parts='[]',completed=0 WHERE id=?",
                            ("expired" if expired else row["status"], identity),
                        )
                else:
                    deleted += self.clean_job(row, keep=part_names(row))
                    if row["status"] == "working":
                        db.execute("UPDATE jobs SET status='queued' WHERE id=?", (identity,))
            finally:
                os.close(lock)
        db.commit()
        return deleted


def reservation(items):
    # ZIP overhead + one raw spool and one verified archive-fallback copy.
    return (
        sum(i["size"] for i in items)
        + 2 * max((i["size"] for i in items), default=0)
        + len(items) * 4096
        + 1024**2
    )


def reap_exports(path):
    with ExportStore(path) as store, store.locked(), connect(path) as db:
        return store.reap(db)
