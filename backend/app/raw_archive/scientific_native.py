"""Explicitly pinned native framing opt-in; Python remains the reference parser."""

import ctypes
import hashlib
import os
import re
import stat
import sys
from pathlib import Path

from .policy import ArchiveBlocked


def counter_factory(library, expected_sha256):
    return _counter_class(_load_pinned_library(library, expected_sha256))


def scanner_factory(library, expected_sha256):
    """Use only an independently reviewed, root-built and SHA-pinned library."""
    return _scanner_class(_load_pinned_library(library, expected_sha256))


def _load_pinned_library(library, expected_sha256):
    path = Path(library)
    if (
        not path.is_absolute()
        or path.is_symlink()
        or path.resolve(strict=False) != path
        or type(expected_sha256) is not str
        or re.fullmatch(r"[a-f0-9]{64}", expected_sha256) is None
    ):
        raise ArchiveBlocked("Pinned absolute native validator required")
    for parent in path.parents:
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ArchiveBlocked("Root-protected native validator ancestry required")
    before = path.lstat()
    if not (
        stat.S_ISREG(before.st_mode)
        and before.st_uid == 0
        and before.st_nlink == 1
        and stat.S_IMODE(before.st_mode) == 0o400
        and 0 < before.st_size <= 128 * 1024
    ):
        raise ArchiveBlocked("Immutable root native validator required")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    body = os.fdopen(fd, "rb")
    try:
        opened = os.fstat(body.fileno())
        raw = body.read(128 * 1024 + 1)
        fields = (
            "st_dev",
            "st_ino",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
            "st_uid",
            "st_mode",
            "st_nlink",
        )
        if (
            any(getattr(before, key) != getattr(opened, key) for key in fields)
            or len(raw) != before.st_size
            or hashlib.sha256(raw).hexdigest() != expected_sha256
        ):
            raise ArchiveBlocked("Native validator digest differs")
        # Linux loads the exact open inode, never a path replaced after hashing.
        load_path = (
            f"/proc/self/fd/{body.fileno()}" if sys.platform.startswith("linux") else str(path)
        )
        try:
            loaded = ctypes.CDLL(load_path)
        except (OSError, ValueError):
            raise ArchiveBlocked("Native validator cannot be loaded") from None
        after, current = os.fstat(body.fileno()), path.lstat()
        if any(getattr(before, key) != getattr(after, key) for key in fields) or any(
            getattr(before, key) != getattr(current, key) for key in fields
        ):
            raise ArchiveBlocked("Native validator generation changed")
        # Keep the exact descriptor alive so dlopen cannot reuse its /proc name
        # for another library while this class still references the handle.
        loaded._pinned_file = body
    except BaseException:
        body.close()
        raise
    return loaded


def _counter_class(loaded):
    """Configure the already verified library; separated for parser equivalence tests."""
    if any(
        not hasattr(loaded, name)
        for name in ("gm_counter_size", "gm_counter_init", "gm_counter_feed", "gm_counter_finish")
    ):
        raise ArchiveBlocked("Native validator interface is incomplete")
    loaded.gm_counter_size.argtypes = []
    loaded.gm_counter_size.restype = ctypes.c_size_t
    loaded.gm_counter_init.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    loaded.gm_counter_feed.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
    loaded.gm_counter_finish.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint64)]
    for name in ("gm_counter_init", "gm_counter_feed", "gm_counter_finish"):
        getattr(loaded, name).restype = ctypes.c_int
    size = loaded.gm_counter_size()
    if not 1 <= size <= 256:
        raise ArchiveBlocked("Native validator state exceeds bound")

    class NativeCounter:
        def __init__(self, columns):
            if type(columns) is not int or not 1 <= columns <= 32767:
                raise ArchiveBlocked("Invalid science column count")
            self.state = ctypes.create_string_buffer(size)
            if loaded.gm_counter_init(self.state, columns):
                raise ArchiveBlocked("Native validator initialization failed")

        def feed(self, raw):
            if not isinstance(raw, bytes) or len(raw) > 2 * 1024**2:
                raise ArchiveBlocked("Bounded native validator input required")
            if loaded.gm_counter_feed(self.state, raw, len(raw)):
                raise ArchiveBlocked("Invalid native COPY framing")

        def finish(self):
            rows = ctypes.c_uint64()
            if loaded.gm_counter_finish(self.state, ctypes.byref(rows)):
                raise ArchiveBlocked("Incomplete native COPY framing")
            return rows.value

    return NativeCounter


def _scanner_class(loaded):
    """Return bounded row ends relative to each supplied input block."""
    counter = _counter_class(loaded)
    try:
        scan = loaded.gm_counter_scan_rows
    except AttributeError:
        raise ArchiveBlocked("Pinned native validator lacks boundary scanning") from None
    scan.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    scan.restype = ctypes.c_int

    class NativeScanner(counter):
        def scan_rows(self, raw, *, capacity=20000):
            if not isinstance(raw, bytes) or len(raw) > 2 * 1024**2:
                raise ArchiveBlocked("Bounded native scanner input required")
            if type(capacity) is not int or not 1 <= capacity <= 20000:
                raise ArchiveBlocked("Bounded native row output required")
            consumed, count = ctypes.c_size_t(), ctypes.c_size_t()
            ends = (ctypes.c_uint32 * capacity)()
            if scan(
                self.state,
                raw,
                len(raw),
                ctypes.byref(consumed),
                ends,
                capacity,
                ctypes.byref(count),
            ):
                raise ArchiveBlocked("Invalid native COPY framing")
            if consumed.value > len(raw) or count.value > capacity or (raw and not consumed.value):
                raise ArchiveBlocked("Native boundary scanner output differs")
            result = list(ends[: count.value])
            if any(
                end <= (result[i - 1] if i else 0) or end > consumed.value
                for i, end in enumerate(result)
            ):
                raise ArchiveBlocked("Native row boundaries are invalid")
            return consumed.value, result

    return NativeScanner
