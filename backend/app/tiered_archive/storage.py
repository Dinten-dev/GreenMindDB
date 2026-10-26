"""Content-addressed SFTP objects and bounded concurrent read cache."""

import fcntl
import os
import re
import shutil
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from app.raw_archive.policy import ArchiveBlocked, RemoteMissing, checksum

MAX_BYTES = 64 * 1024**2


def disk_reserve(path, needed):
    if shutil.disk_usage(path).free < 2 * 1024**3 + needed:
        raise ArchiveBlocked("Archive disk safety reserve exhausted")


def validate_key(key):
    if not re.fullmatch(
        r"(?:production|staging)/tiered/[a-f0-9-]{36}/[a-z][a-z0-9_]{0,63}/"
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}/[a-f0-9]{64}\.(?:parquet|json|wav|bin|gz)",
        key,
    ):
        raise ArchiveBlocked("Unsafe tiered archive path")
    return key


class Store:
    def __init__(self, transport):
        self.transport = transport

    @property
    def identity(self):
        return self.transport.identity

    def download(self, key, path, size, *, snapshot=None):
        if "/tiered/" not in key:
            if snapshot is None:
                return self.transport.download(key, path, size)
            return self.transport.download_snapshot(snapshot, key, path, size)
        validate_key(key)
        if snapshot is None and hasattr(self.transport, "download_tiered"):
            return self.transport.download_tiered(key, path, size)
        remote = self.transport.root + "/" + key
        if snapshot is not None:
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", snapshot):
                raise ArchiveBlocked("Unsafe snapshot path")
            remote = f"/home/.zfs/snapshot/{snapshot}/" + remote
        self.transport._download_path(remote, path, size)

    def publish(self, key, path):
        if "/tiered/" not in key:
            self.transport.path(key)
            return self.transport.publish(path, key)
        validate_key(key)
        size, sha = path.stat().st_size, checksum(path)
        if key.rsplit("/", 1)[1].split(".", 1)[0] != sha or not 0 < size <= MAX_BYTES:
            raise ArchiveBlocked("Archive must be bounded and content-addressed")
        with tempfile.TemporaryDirectory(dir=path.parent) as folder:
            returned = Path(folder) / "returned"
            try:
                self.download(key, returned, size)
            except RemoteMissing:
                remote = self.transport.root + "/" + key
                temporary = remote + ".partial-" + uuid.uuid4().hex
                directories = remote.split("/")[:-1]
                commands = [
                    "-mkdir " + "/".join(directories[:n]) for n in range(1, len(directories) + 1)
                ]
                commands += [
                    f"put {self.transport.quote(path)} {temporary}",
                    f"rename {temporary} {remote}",
                ]
                self.transport.run("\n".join(commands) + "\n")
                self.download(key, returned, size)
            if returned.stat().st_size != size or checksum(returned) != sha:
                raise ArchiveBlocked("Independent SFTP readback differs")


class Cache:
    def __init__(self, root, *, maximum_bytes=512 * 1024**2, ttl=86400):
        self.root = Path(root)
        if not self.root.is_absolute() or self.root.is_symlink() or maximum_bytes < MAX_BYTES:
            raise ArchiveBlocked("Bounded isolated cache required")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.maximum_bytes, self.ttl = maximum_bytes, ttl

    @contextmanager
    def file(self, reference, store):
        sha, size = reference["sha256"], reference["bytes"]
        if not re.fullmatch(r"[a-f0-9]{64}", sha) or not 0 < size <= MAX_BYTES:
            raise ArchiveBlocked("Invalid cache reference")
        # One global cross-process lock bounds disk usage and prevents eviction
        # while DuckDB holds a file. Archive requests deliberately queue here.
        with (self.root / "cache.lock").open("a") as lock:
            deadline = time.monotonic() + 10
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ArchiveBlocked("Archive cache reader busy") from None
                    time.sleep(0.05)
            for abandoned in self.root.glob("*.partial-*"):
                if (
                    re.fullmatch(r"[a-f0-9]{64}\.partial-[a-f0-9]{32}", abandoned.name)
                    and abandoned.is_file()
                    and not abandoned.is_symlink()
                ):
                    abandoned.unlink()
            path = self.root / (sha + ".cache")
            if path.is_symlink():
                raise ArchiveBlocked("Symlink in archive cache")
            if path.exists() and (path.stat().st_size != size or checksum(path) != sha):
                path.unlink()
            self.prune(reserve=0 if path.exists() else size, keep=path)
            if not path.exists():
                disk_reserve(self.root, size)
                temporary = self.root / (sha + ".partial-" + uuid.uuid4().hex)
                try:
                    store.download(reference["key"], temporary, size)
                    if temporary.stat().st_size != size or checksum(temporary) != sha:
                        raise ArchiveBlocked("Archive cache download differs")
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
            os.utime(path, None)
            yield path

    def prune(self, *, reserve, keep):
        files = [p for p in self.root.glob("*.cache") if p.is_file() and not p.is_symlink()]
        total = sum(p.stat().st_size for p in files)
        for path in sorted(files, key=lambda p: p.stat().st_mtime):
            if path == keep:
                continue
            if (
                total + reserve > self.maximum_bytes
                or time.time() - path.stat().st_mtime > self.ttl
            ):
                total -= path.stat().st_size
                path.unlink()
        if total + reserve > self.maximum_bytes:
            raise ArchiveBlocked("Archive cache budget exhausted")
