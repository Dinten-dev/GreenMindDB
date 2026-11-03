"""Evidence-bound Science-V3 scope bootstrap; never mutates Source data.

Source creates a bounded handoff from a completed frozen capture. Archive
validates bound Archive/Source locks and root handoff before creating a new,
separate ledger. This is not a science backup or a writer approval.
"""

import hashlib
import json
import os
import re
import stat
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.raw_archive.policy import ArchiveBlocked

from . import scientific_capture as capture
from . import scientific_parts as parts


def need(value, message):
    if not value:
        raise ArchiveBlocked(message)


def _code_sha(module):
    path = Path(module.__file__).resolve(strict=True)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_lock(*, full):
    """Observe the actual Source jobs.lock holder and its OS process generation."""
    import pwd

    path = Path("/var/lib/greenmind-archive-coordination/jobs.lock")
    info = path.lstat()
    need(
        stat.S_ISREG(info.st_mode)
        and info.st_uid == 0
        and info.st_nlink == 1
        and not info.st_mode & 0o007
        and (info.st_dev, info.st_ino) == (2049, 393144),
        "Source jobs.lock identity differs",
    )
    device = (os.major(info.st_dev), os.minor(info.st_dev), info.st_ino)
    holders = []
    for entry in Path("/proc/locks").read_text().splitlines():
        fields = entry.split()
        if len(fields) < 8 or fields[1:4] != ["FLOCK", "ADVISORY", "WRITE"]:
            continue
        addr = fields[5].split(":")
        if (
            len(addr) == 3
            and (int(addr[0], 16), int(addr[1], 16), int(addr[2])) == device
            and fields[6:8] == ["0", "EOF"]
        ):
            holders.append(int(fields[4]))
    need(len(holders) == 1 and holders[0] > 1, "Source jobs.lock has no unique live holder")
    pid = holders[0]
    uid = pwd.getpwnam("greenmind-archive-lease").pw_uid
    lines = Path(f"/proc/{pid}/status").read_text().splitlines()
    ids = next(line.split()[1:] for line in lines if line.startswith("Uid:"))
    need(
        len(ids) == 4 and all(item.isdecimal() and int(item) == uid for item in ids),
        "Source lease holder UID differs",
    )
    ticks = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19])
    result = {
        "device": info.st_dev,
        "inode": info.st_ino,
        "pid": pid,
        "uid": uid,
        "start_ticks": ticks,
    }
    if full:
        command = Path(f"/proc/{pid}/cmdline").read_bytes()
        need(0 < len(command) <= 8192, "Source lease command unavailable")
        result["command_sha256"] = hashlib.sha256(command).hexdigest()
        digest = hashlib.sha256()
        with Path(f"/proc/{pid}/exe").open("rb") as executable:
            while block := executable.read(65536):
                digest.update(block)
        result["executable_sha256"] = digest.hexdigest()
    return result


def _private_json(path, expected, limit=1024 * 1024):
    from app.tiered_archive.ledger import _private_read
    from app.tiered_archive.restore_inputs import frozen

    need(
        type(expected) is str and re.fullmatch(r"[a-f0-9]{64}", expected),
        "Pinned root evidence required",
    )
    path = Path(path)
    before = frozen(path)
    raw = _private_read(path, limit)
    need(
        frozen(path) == before and hashlib.sha256(raw).hexdigest() == expected,
        "Frozen root evidence differs",
    )
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise ArchiveBlocked("Root evidence encoding differs") from exc
    need(type(value) is dict, "Root evidence object required")
    return value


def _publish_checked_frame(output, raw, check):
    """Publish only a fully fsynced frame after a fresh Source lease check."""
    pending = output.with_name(output.name + ".pending-" + uuid.uuid4().hex)
    try:
        parts._write_immutable(pending, raw)
        check()
        os.link(pending, output, follow_symlinks=False)
        pending.unlink()
        fd = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if pending.exists():
            pending.unlink()


def source_handoff(
    source,
    capture_sha256,
    index_file,
    index_sha256,
    output,
    *,
    lease_session,
    checkpoint,
):
    """Source-root producer; verifies actual complete native files before emitting a frame.

    Caller holds the real source lease and supplies a checkpoint. Expiry uses
    Source-local lock-holder start ticks, never the Archive clock.
    """
    import socket
    import time

    started = time.monotonic()
    need(
        os.geteuid() == 0
        and callable(checkpoint)
        and isinstance(lease_session, str)
        and re.fullmatch(r"[a-f0-9]{32}", lease_session),
        "Current root source lease required",
    )
    source, output = Path(source), Path(output)
    capture._source_resources(source.parent)
    need(output.parent == source.parent, "Handoff must remain on private source volume")
    locked = _source_lock(full=True)
    ticks_per_second = os.sysconf("SC_CLK_TCK")
    need(type(ticks_per_second) is int and ticks_per_second > 0, "Source clock ticks unavailable")
    lease_deadline = min(started + 95, locked["start_ticks"] / ticks_per_second + 110)
    need(time.monotonic() < lease_deadline, "Source lease or job deadline already expired")
    expected_holder = {key: locked[key] for key in ("device", "inode", "pid", "uid", "start_ticks")}
    last_lock_check = [0.0]

    def check():
        need(time.monotonic() < lease_deadline, "Source handoff lease expired")
        checkpoint()
        if time.monotonic() - last_lock_check[0] >= 1:
            capture._cgroup_limits()
            need(_source_lock(full=False) == expected_holder, "Source lease holder changed")
            last_lock_check[0] = time.monotonic()
        need(time.monotonic() < lease_deadline, "Source handoff lease expired")

    proof, index = parts.load_index(
        source,
        capture_sha256,
        index_file,
        index_sha256,
        checkpoint=check,
        cooperative_pause=0.25,
    )
    machine = hashlib.sha256(Path("/etc/machine-id").read_bytes()).hexdigest()
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    need(re.fullmatch(r"[a-f0-9-]{36}", boot), "Source boot identity unavailable")
    check()
    capture._cgroup_limits()
    need(_source_lock(full=True) == locked, "Source lease process generation changed")
    frame = {
        "format": "greenmind.science-source-handoff.v1",
        "source": "gateway",
        "capture_sha256": capture_sha256,
        "index_sha256": index_sha256,
        "capture_id": proof["capture_id"],
        "scope_sha256": proof["scope_sha256"],
        "lease_session": lease_session,
        "native_files": [
            {"table": x["table"], "rows": x["rows"], "bytes": x["bytes"], "sha256": x["sha256"]}
            for x in proof["files"]
        ],
        "part_count": len(index["parts"]),
        "hostname": socket.gethostname(),
        "machine_sha256": machine,
        "boot_id": boot,
        "uid": 0,
        "source_lock": locked,
        "producer_sha256": _code_sha(__import__(__name__, fromlist=["x"])),
        "parts_code_sha256": _code_sha(parts),
        "capture_code_sha256": _code_sha(capture),
        "observed_at": datetime.now(UTC).isoformat(),
    }
    raw = (json.dumps(frame, sort_keys=True, separators=(",", ":")) + "\n").encode()
    _publish_checked_frame(output, raw, check)
    return {"frame_sha256": hashlib.sha256(raw).hexdigest(), "capture_id": proof["capture_id"]}


def source_lease_handoff(output, *, lease_session, checkpoint):
    """Emit a fresh Source-root lock/host generation frame without capture IO."""
    import socket
    import sys
    import time

    start = time.monotonic()
    need(
        os.geteuid() == 0
        and callable(checkpoint)
        and isinstance(lease_session, str)
        and re.fullmatch(r"[a-f0-9]{32}", lease_session),
        "Current root source lease required",
    )
    output = Path(output)
    capture._source_resources(output.parent)
    locked = _source_lock(full=True)
    ticks = os.sysconf("SC_CLK_TCK")
    need(type(ticks) is int and ticks > 0, "Source clock ticks unavailable")
    deadline = min(start + 95, locked["start_ticks"] / ticks + 110)
    checkpoint()
    need(
        time.monotonic() < deadline and _source_lock(full=True) == locked,
        "Source lease process generation changed or expired",
    )
    frame = {
        "format": "greenmind.science-lease-handoff.v1",
        "lease_session": lease_session,
        "source_lock": locked,
        "hostname": socket.gethostname(),
        "machine_sha256": hashlib.sha256(Path("/etc/machine-id").read_bytes()).hexdigest(),
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "uid": 0,
        "producer_sha256": _code_sha(sys.modules[__name__]),
        "observed_at": datetime.now(UTC).isoformat(),
    }
    need(re.fullmatch(r"[a-f0-9-]{36}", frame["boot_id"]), "Source boot identity unavailable")
    raw = (json.dumps(frame, sort_keys=True, separators=(",", ":")) + "\n").encode()

    def check():
        checkpoint()
        capture._cgroup_limits()
        need(time.monotonic() < deadline, "Source lease expired")
        need(
            _source_lock(full=False)
            == {key: locked[key] for key in ("device", "inode", "pid", "uid", "start_ticks")},
            "Source lease process generation changed",
        )
        need(time.monotonic() < deadline, "Source lease expired")

    _publish_checked_frame(output, raw, check)
    return {"frame_sha256": hashlib.sha256(raw).hexdigest()}


def produce_challenge_frame(challenge, output, *, checkpoint, source=None, index_file=None):
    """Source-root bridge entrypoint for one authenticated Archive challenge."""
    need(
        type(challenge) is bytes and 0 < len(challenge) <= 16384, "Bounded root challenge required"
    )
    try:
        value = json.loads(challenge)
    except ValueError as exc:
        raise ArchiveBlocked("Source challenge encoding differs") from exc
    need(
        type(value) is dict
        and set(value) == {"format", "action", "lease_session", "payload"}
        and value["format"] == "greenmind.science-source-challenge.v1"
        and value["action"] in {"capture-index", "lease-lock"}
        and type(value["payload"]) is dict,
        "Exact Source root challenge required",
    )
    if value["action"] == "lease-lock":
        need(
            not value["payload"] and source is None and index_file is None,
            "Source lease-only challenge differs",
        )
        return source_lease_handoff(
            output, lease_session=value["lease_session"], checkpoint=checkpoint
        )
    need(
        set(value["payload"]) == {"capture_sha256", "index_sha256"}
        and all(
            isinstance(value["payload"][key], str)
            and re.fullmatch(r"[a-f0-9]{64}", value["payload"][key])
            for key in ("capture_sha256", "index_sha256")
        )
        and source is not None
        and index_file is not None,
        "Source capture challenge differs",
    )
    return source_handoff(
        source,
        value["payload"]["capture_sha256"],
        index_file,
        value["payload"]["index_sha256"],
        output,
        lease_session=value["lease_session"],
        checkpoint=checkpoint,
    )


def validate_lease_handoff(path, expected_sha256, binding, lease_session):
    """Bind an authenticated fresh Source-root lock frame to this exact lease."""
    import sys

    frame = _private_json(path, expected_sha256)
    fields = {
        "format",
        "lease_session",
        "source_lock",
        "hostname",
        "machine_sha256",
        "boot_id",
        "uid",
        "producer_sha256",
        "observed_at",
    }
    try:
        observed = datetime.fromisoformat(frame.get("observed_at", ""))
    except (TypeError, ValueError) as exc:
        raise ArchiveBlocked("Source lease frame time differs") from exc
    lock = frame.get("source_lock")
    need(
        set(frame) == fields
        and frame["format"] == "greenmind.science-lease-handoff.v1"
        and frame["lease_session"] == lease_session
        and frame["uid"] == 0
        and frame["hostname"] == binding.get("hostname")
        and frame["machine_sha256"] == binding.get("machine_sha256")
        and frame["boot_id"] == binding.get("boot_id")
        and frame["producer_sha256"] == _code_sha(sys.modules[__name__])
        and observed.utcoffset() is not None
        and timedelta(0) <= datetime.now(UTC) - observed <= timedelta(minutes=5)
        and type(lock) is dict
        and set(lock)
        == {"device", "inode", "pid", "uid", "start_ticks", "command_sha256", "executable_sha256"}
        and lock["device"] == binding.get("lock_device")
        and lock["inode"] == binding.get("lock_inode")
        and lock["uid"] == binding.get("lease_uid")
        and lock["command_sha256"] == binding.get("lease_command_sha256")
        and lock["executable_sha256"] == binding.get("lease_executable_sha256")
        and type(lock["pid"]) is int
        and lock["pid"] > 1
        and type(lock["start_ticks"]) is int
        and lock["start_ticks"] > 0,
        "Fresh Source lease lock frame differs",
    )
    return None


def current_source_session():
    """Return the live ownedwait nonce only to the authenticated root bridge."""
    from app.raw_archive import remote_lease
    from app.raw_archive.coordination import bound_job_lock
    from app.raw_archive.remote_lease import require_bound_source_lease

    bound_job_lock()
    require_bound_source_lease()
    authority = remote_lease._BOUND_SOURCE_LEASE.get()
    check = authority[0] if authority else None
    remote = getattr(check, "__self__", None)
    need(
        type(remote) is remote_lease.RemoteLease and re.fullmatch(r"[a-f0-9]{32}", remote.session),
        "Live production source lease generation required",
    )
    remote.check()
    return remote.session


def _authority(ledger, binding_path, binding_sha256, checkpoint):
    """Check live locks, independent Source identity and actual local intents.

    No managed-snapshot scope or snapshot rotation authority is requested.
    """
    from app.raw_archive import remote_lease
    from app.tiered_archive.ledger import Ledger

    need(
        os.geteuid() == 0 and type(ledger) is Ledger and not ledger.read_only,
        "Root archive authority ledger required",
    )
    session = current_source_session()
    checkpoint()
    binding = _private_json(binding_path, binding_sha256)
    fields = {
        "format",
        "hostname",
        "machine_sha256",
        "boot_id",
        "lease_host",
        "lease_known_hosts_sha256",
        "lock_device",
        "lock_inode",
        "lease_uid",
        "lease_command_sha256",
        "lease_executable_sha256",
    }
    need(
        set(binding) == fields
        and binding["format"] == "greenmind.science-source-authority.v1"
        and isinstance(binding["hostname"], str)
        and 0 < len(binding["hostname"]) <= 255
        and isinstance(binding["machine_sha256"], str)
        and re.fullmatch(r"[a-f0-9]{64}", binding["machine_sha256"])
        and isinstance(binding["boot_id"], str)
        and re.fullmatch(r"[a-f0-9-]{36}", binding["boot_id"])
        and binding["lease_host"] == os.environ.get("RAW_ARCHIVE_LEASE_HOST")
        and binding["lock_device"] == 2049
        and binding["lock_inode"] == 393144
        and type(binding["lease_uid"]) is int
        and binding["lease_uid"] > 0
        and all(
            type(binding[key]) is str and re.fullmatch(r"[a-f0-9]{64}", binding[key])
            for key in ("lease_command_sha256", "lease_executable_sha256")
        )
        and os.environ.get("RAW_ARCHIVE_LEASE_USER") == "greenmind-archive-lease",
        "Pinned live source authority differs",
    )
    known = remote_lease.private_file(os.environ["RAW_ARCHIVE_LEASE_KNOWN_HOSTS"])
    need(
        hashlib.sha256(known.read_bytes()).hexdigest() == binding["lease_known_hosts_sha256"],
        "Source lease host-key pin differs",
    )
    for table in ("science_v3_part", "science_v3_whole"):
        if ledger.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone():
            need(
                not ledger.db.execute(
                    "SELECT 1 FROM " + table + " WHERE state='prepared' LIMIT 1"
                ).fetchone(),
                "Unresolved science operation prevents fresh scope",
            )
    need(
        not ledger.db.execute(
            "SELECT 1 FROM bundle WHERE json_extract(receipt,'$.state')='deleting' AND json_extract(receipt,'$.source.kind')!='wav' LIMIT 1"
        ).fetchone(),
        "Unresolved SQL deletion intent prevents fresh scope",
    )
    checkpoint()
    need(current_source_session() == session, "Source lease generation changed")
    return session, binding


def _frame(
    metadata_dir,
    capture_sha256,
    index_file,
    index_sha256,
    frame_path,
    frame_sha256,
    root_binding,
    lease_session,
    checkpoint,
):
    frame = _private_json(frame_path, frame_sha256)
    proof, index = parts.load_index(
        metadata_dir,
        capture_sha256,
        index_file,
        index_sha256,
        checkpoint=checkpoint,
        verify_files=False,
    )
    fields = {
        "format",
        "source",
        "capture_sha256",
        "index_sha256",
        "capture_id",
        "scope_sha256",
        "native_files",
        "part_count",
        "hostname",
        "machine_sha256",
        "boot_id",
        "uid",
        "lease_session",
        "source_lock",
        "producer_sha256",
        "parts_code_sha256",
        "capture_code_sha256",
        "observed_at",
    }
    expected_files = [
        {"table": x["table"], "rows": x["rows"], "bytes": x["bytes"], "sha256": x["sha256"]}
        for x in proof["files"]
    ]
    import sys

    observed = datetime.fromisoformat(frame.get("observed_at", ""))
    now = datetime.now(UTC)
    need(
        set(frame) == fields
        and frame["format"] == "greenmind.science-source-handoff.v1"
        and frame["source"] == "gateway"
        and frame["uid"] == 0
        and frame["capture_sha256"] == capture_sha256
        and frame["index_sha256"] == index_sha256
        and frame["capture_id"] == proof["capture_id"]
        and frame["scope_sha256"] == proof["scope_sha256"]
        and frame["lease_session"] == lease_session
        and frame["native_files"] == expected_files
        and frame["part_count"] == len(index["parts"])
        and frame["hostname"] == root_binding.get("hostname")
        and frame["machine_sha256"] == root_binding.get("machine_sha256")
        and frame["boot_id"] == root_binding.get("boot_id")
        and type(frame["source_lock"]) is dict
        and set(frame["source_lock"])
        == {"device", "inode", "pid", "uid", "start_ticks", "command_sha256", "executable_sha256"}
        and frame["source_lock"]["device"] == root_binding.get("lock_device")
        and frame["source_lock"]["inode"] == root_binding.get("lock_inode")
        and frame["source_lock"]["uid"] == root_binding.get("lease_uid")
        and frame["source_lock"]["command_sha256"] == root_binding.get("lease_command_sha256")
        and frame["source_lock"]["executable_sha256"] == root_binding.get("lease_executable_sha256")
        and type(frame["source_lock"]["pid"]) is int
        and frame["source_lock"]["pid"] > 1
        and type(frame["source_lock"]["start_ticks"]) is int
        and frame["source_lock"]["start_ticks"] > 0
        and frame["producer_sha256"] == _code_sha(sys.modules[__name__])
        and frame["parts_code_sha256"] == _code_sha(parts)
        and frame["capture_code_sha256"] == _code_sha(capture)
        and observed.utcoffset() is not None
        and timedelta(0) <= now - observed <= timedelta(minutes=5)
        and observed >= datetime.fromisoformat(proof["finished_at"]),
        "Fresh verified source capture handoff differs",
    )
    return proof, index


def _new_science_dir(new_root):
    from app.tiered_archive.restore_inputs import protected_parents

    new_root = Path(new_root)
    need(
        new_root.is_absolute()
        and new_root.resolve(strict=False) == new_root
        and not os.path.lexists(new_root),
        "Entirely new science ledger directory required",
    )
    protected_parents(new_root)
    new_root.mkdir(mode=0o700)
    need(
        new_root.stat().st_uid == 0 and stat.S_IMODE(new_root.stat().st_mode) == 0o700,
        "New root-private science ledger required",
    )
    return new_root


def bootstrap_science(
    new_root,
    authority_ledger,
    *,
    source_binding_path,
    source_binding_sha256,
    metadata_dir,
    capture_sha256,
    index_file,
    index_sha256,
    frame_path,
    frame_sha256,
    checkpoint,
):
    """Create one new independent Science ledger under live root-scope authority."""
    from app.tiered_archive.ledger import Ledger

    lease_session, binding = _authority(
        authority_ledger, source_binding_path, source_binding_sha256, checkpoint
    )
    proof, _ = _frame(
        metadata_dir,
        capture_sha256,
        index_file,
        index_sha256,
        frame_path,
        frame_sha256,
        binding,
        lease_session,
        checkpoint,
    )
    new_root = _new_science_dir(new_root)
    target = Ledger(new_root)
    try:
        need(
            not target.status() and not target.db.execute("SELECT 1 FROM event LIMIT 1").fetchone(),
            "New empty science ledger required",
        )
        epoch = uuid.uuid4().hex
        with target.db:
            target.db.execute(
                "CREATE TABLE science_v3_scope (id INTEGER PRIMARY KEY CHECK(id=1), state TEXT NOT NULL)"
            )
            target.db.execute(
                "CREATE TABLE science_v3_scope_epoch (id INTEGER PRIMARY KEY CHECK(id=1), epoch TEXT NOT NULL, source_session TEXT NOT NULL, capture_id TEXT NOT NULL, capture_sha256 TEXT NOT NULL, index_sha256 TEXT NOT NULL, frame_sha256 TEXT NOT NULL, created_at TEXT NOT NULL)"
            )
            target.db.execute("INSERT INTO science_v3_scope VALUES (1,'current')")
            target.db.execute(
                "INSERT INTO science_v3_scope_epoch VALUES (1,?,?,?,?,?,?,?)",
                (
                    epoch,
                    lease_session,
                    proof["capture_id"],
                    capture_sha256,
                    index_sha256,
                    frame_sha256,
                    datetime.now(UTC).isoformat(),
                ),
            )
            # Never infer an unused SQL budget from a new, separate science ledger.
            target.db.execute(
                "CREATE TABLE sql_deletion_scope (id INTEGER PRIMARY KEY CHECK(id=1), state TEXT NOT NULL)"
            )
            target.db.execute("INSERT INTO sql_deletion_scope VALUES (1,'needs_reconcile')")
        target.publish_read_view()
        checkpoint()
        return {
            "status": "SCIENCE_SCOPE_CURRENT",
            "scope_epoch": epoch,
            "capture_id": proof["capture_id"],
            "sql_scope": "HOLD",
            "writer_allowed": False,
        }
    finally:
        target.close()
