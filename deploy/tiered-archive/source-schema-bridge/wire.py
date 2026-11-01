"""Bounded private pipe protocol. Never emits payloads or free error strings."""

import hashlib
import json
import os
import select
import stat
import time
from pathlib import Path

MAX_BYTES = 64 * 1024**2
BLOCK = 65536


class Closed(RuntimeError):
    pass


def need(value, code):
    if not value:
        raise Closed(code)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def read_exact(fd, count, deadline):
    data = bytearray()
    while len(data) < count:
        need(time.monotonic() < deadline, "pipe_deadline")
        if not select.select([fd], [], [], min(0.2, max(0, deadline - time.monotonic())))[0]:
            continue
        block = os.read(fd, min(BLOCK, count - len(data)))
        need(block, "pipe_eof")
        data.extend(block)
    return bytes(data)


def write_all(fd, data, deadline):
    os.set_blocking(fd, False)
    view = memoryview(data)
    while view:
        need(time.monotonic() < deadline, "pipe_deadline")
        if not select.select([], [fd], [], min(0.2, max(0, deadline - time.monotonic())))[1]:
            continue
        try:
            sent = os.write(fd, view[:BLOCK])
        except BlockingIOError:
            continue
        need(sent > 0, "pipe_write_failed")
        view = view[sent:]


def read_json(fd, deadline):
    value = bytearray()
    while True:
        char = read_exact(fd, 1, deadline)
        if char == b"\n":
            break
        value.extend(char)
        need(len(value) <= 32768, "control_frame_bound")
    return json.loads(value)


def write_json(fd, value, deadline):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    need(len(raw) <= 32768, "control_frame_bound")
    write_all(fd, raw + b"\n", deadline)


def private(path, *, directory=False):
    path = Path(path)
    need(path.is_absolute() and path.resolve() == path, "private_path")
    info = path.stat()
    need(info.st_uid == 0, "private_owner")
    if directory:
        need(
            stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700, "private_directory"
        )
    else:
        need(
            stat.S_ISREG(info.st_mode)
            and info.st_nlink == 1
            and stat.S_IMODE(info.st_mode) == 0o600,
            "private_file",
        )
    return path


def startup(unit):
    need(os.geteuid() == 0, "root_only")
    group = Path("/sys/fs/cgroup/system.slice") / unit
    need(Path("/proc/self/cgroup").read_text().strip() == "0::/system.slice/" + unit, "own_unit")
    for name, expected in (
        ("memory.max", str(64 * 1024**2)),
        ("memory.high", str(56 * 1024**2)),
        ("memory.swap.max", "0"),
        ("cpu.max", "5000 100000"),
    ):
        need((group / name).read_text().strip() == expected, "own_resource_limit")
    info = group.stat()
    return {
        "pid": os.getpid(),
        "ticks": int(Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()[19]),
        "cgroup": str(group),
        "device": info.st_dev,
        "inode": info.st_ino,
        "invocation": os.environ["INVOCATION_ID"],
    }
