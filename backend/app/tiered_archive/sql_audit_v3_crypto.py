"""Load only an explicitly pinned, Source-root private offline crypto closure."""

import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

from app.raw_archive.policy import ArchiveBlocked

MANIFEST_SHA256 = "e6ac2089c654b77c7e9262579a6ba9563f8d7ca4bdd6efbc96fe9287fe99cfda"

def activate(directory, manifest_sha256, *, checkpoint):
    def need(value):
        if not value:
            raise ArchiveBlocked("Source offline cryptography runtime differs")

    root = Path(directory)
    need(callable(checkpoint) and os.geteuid() == 0 and root.is_absolute() and root.resolve() == root
         and type(manifest_sha256) is str and manifest_sha256 == MANIFEST_SHA256)
    info = root.lstat()
    need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700)
    for parent in root.parents:
        info = parent.lstat()
        need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    path = root / "manifest.json"
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        need(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o400
             and info.st_nlink == 1 and info.st_size <= 131072)
        raw = stream.read(131073)
    need(hashlib.sha256(raw).hexdigest() == manifest_sha256)
    manifest = json.loads(raw)
    need(type(manifest) is dict and set(manifest) == {"format", "files"}
         and manifest["format"] == "greenmind.sql-source-crypto-runtime.v1"
         and type(manifest["files"]) is dict and 1 <= len(manifest["files"]) <= 512)
    total = 0
    for name, expected in manifest["files"].items():
        checkpoint()
        rel = Path(name)
        need(type(name) is str and not rel.is_absolute() and ".." not in rel.parts and rel.as_posix() == name
             and type(expected) is dict and set(expected) == {"bytes", "sha256"}
             and type(expected["bytes"]) is int and 0 <= expected["bytes"] <= 20 * 1024**2
             and type(expected["sha256"]) is str and re.fullmatch(r"[a-f0-9]{64}", expected["sha256"]))
        for parent in (root / rel).parents:
            if parent == root:
                break
            info = parent.lstat()
            need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700)
        fd = os.open(root / rel, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            need(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and stat.S_IMODE(before.st_mode) == 0o400
                 and before.st_nlink == 1 and before.st_size == expected["bytes"])
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
            after = os.fstat(stream.fileno())
            if sys.platform.startswith("linux"):
                need(hasattr(os, "posix_fadvise") and hasattr(os, "POSIX_FADV_DONTNEED"))
                os.posix_fadvise(stream.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
        need((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
             == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
             and digest == expected["sha256"])
        total += before.st_size
        need(total <= 32 * 1024**2)
    actual = set()
    for path in root.rglob("*"):
        info = path.lstat()
        need(not stat.S_ISLNK(info.st_mode))
        if not stat.S_ISDIR(info.st_mode):
            actual.add(path.relative_to(root).as_posix())
    need(actual == set(manifest["files"]) | {"manifest.json"})
    need(not any(name == "cryptography" or name.startswith("cryptography.") or name == "_cffi_backend" for name in sys.modules))
    checkpoint()
    sys.path.insert(0, str(root))
