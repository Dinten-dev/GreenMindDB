"""V3 full-column native source capture. No writer, key, transport or publication.

Call only from the existing source-lease owner; a completed private capture is
resumable offline. A failed SQL capture has no completion manifest.
"""

import hashlib
import json
import os
import re
import shutil
import stat
import struct
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

from . import catalog_capture
from .policy import ArchiveBlocked
from .scientific_scope_v3 import SCOPE_SQL, SOURCE_SCOPE, SOURCE_SCOPE_SHA256

FORMAT = "greenmind.science-native-capture.v3"
CAPTURE_BYTES = catalog_capture.CAPTURE_BYTES
HEADER = catalog_capture.SIGNATURE + struct.pack("!ii", 0, 0)
TRAILER = struct.pack("!h", -1)
ROW_BYTES = 1024 * 1024
SOURCE_VOLUME = Path("/mnt/HC_Volume_106755700")
SOURCE_DEVICE = 2064
TABLES = ("wav_feature", "wav_feature_version")
READ_FLAGS = (
    "RAW_ARCHIVE_DELETE_ENABLED",
    "RETENTION_ENABLED",
    "DIRECT_RETENTION_ENABLED",
    "VISUAL_PRUNE_ENABLED",
    "TIERED_ARCHIVE_DELETE_ENABLED",
    "TIERED_ARCHIVE_SQL_RETENTION_ACCEPTED",
    "TIERED_ARCHIVE_SQL_PRUNE_ENABLED",
)


def need(value, message):
    if not value:
        raise ArchiveBlocked(message)


def digest_json(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def roster():
    need(digest_json(SOURCE_SCOPE) == SOURCE_SCOPE_SHA256, "Pinned V3 scientific catalog differs")
    columns = SOURCE_SCOPE["columns"]
    result = {t: [x for x in columns if x["table_name"] == t] for t in TABLES}
    need([len(result[t]) for t in TABLES] == [62, 10], "Full native science roster required")
    for table, items in result.items():
        need(
            all(
                x["attnum"] == i
                and re.fullmatch("[a-z_][a-z0-9_]*", x["name"])
                and x["archive_reader_select"] is True
                for i, x in enumerate(items, 1)
            ),
            "Exact ordered science columns required",
        )
        need(
            all(x["type"] in catalog_capture.TYPES and x["type_kind"] == "b" for x in items),
            "Unsupported science native type",
        )
        primary = [
            x for x in SOURCE_SCOPE["constraints"] if x["table_name"] == table and x["type"] == "p"
        ]
        need(
            len(primary) == 1 and primary[0]["definition"] == "PRIMARY KEY (id)",
            "Exact science primary key required",
        )
    return result


class FrameCounter:
    """Constant-memory binary COPY framing counter; scientific bytes are never decoded."""

    def __init__(self, columns):
        need(type(columns) is int and 1 <= columns <= 32767, "Invalid science column count")
        self.columns = columns
        self.stage = "header"
        self.buffer = bytearray()
        self.remaining = 0
        self.fields = 0
        self.row_bytes = 0
        self.rows = 0

    def feed(self, raw):
        view = memoryview(raw)
        pos = 0
        while pos < len(view):
            need(self.stage != "done", "Trailing native COPY bytes")
            if self.stage == "value":
                n = min(self.remaining, len(view) - pos)
                pos += n
                self.remaining -= n
                if not self.remaining:
                    self._field_done()
                continue
            size = {"header": len(HEADER), "row": 2, "length": 4}[self.stage]
            n = min(size - len(self.buffer), len(view) - pos)
            self.buffer.extend(view[pos : pos + n])
            pos += n
            if len(self.buffer) != size:
                continue
            value = bytes(self.buffer)
            self.buffer.clear()
            if self.stage == "header":
                need(value == HEADER, "Invalid native COPY header")
                self.stage = "row"
            elif self.stage == "row":
                count = struct.unpack("!h", value)[0]
                if count == -1:
                    self.stage = "done"
                else:
                    need(count == self.columns, "Native COPY column count differs")
                    self.fields = count
                    self.row_bytes = 2
                    self.stage = "length"
            else:
                length = struct.unpack("!i", value)[0]
                need(-1 <= length <= ROW_BYTES, "Invalid native COPY field length")
                self.row_bytes += 4 + max(length, 0)
                need(self.row_bytes <= ROW_BYTES, "Native COPY row exceeds bounded memory")
                self.remaining = max(length, 0)
                if self.remaining:
                    self.stage = "value"
                else:
                    self._field_done()

    def _field_done(self):
        self.fields -= 1
        need(self.fields >= 0, "Unexpected native COPY field")
        if self.fields:
            self.stage = "length"
        else:
            self.rows += 1
            self.stage = "row"

    def finish(self):
        need(
            self.stage == "done" and not self.buffer and not self.remaining,
            "Incomplete native COPY framing",
        )
        return self.rows


class FramedSink(catalog_capture.Sink):
    def __init__(self, body, checkpoint, budget, columns, counter_factory=FrameCounter):
        super().__init__(body, checkpoint, budget)
        need(callable(counter_factory), "Pinned native framing counter factory required")
        self.counter = counter_factory(columns)
        need(
            callable(getattr(self.counter, "feed", None))
            and callable(getattr(self.counter, "finish", None)),
            "Invalid framing counter interface",
        )

    def write(self, block):
        self.counter.feed(block)
        return super().write(block)


def _cgroup_limits():
    """Recheck original Source cgroup bounds and resource events throughout COPY."""
    cg = Path("/sys/fs/cgroup") / next(
        line[3:]
        for line in Path("/proc/self/cgroup").read_text().splitlines()
        if line.startswith("0::")
    ).lstrip("/")
    need(".." not in cg.parts, "Unsafe source cgroup path")
    memory = (cg / "memory.max").read_text().strip()
    cpu = (cg / "cpu.max").read_text().split()
    need(
        memory.isdecimal()
        and 0 < int(memory) <= 128 * 1024**2
        and (cg / "memory.swap.max").read_text().strip() == "0"
        and len(cpu) == 2
        and all(v.isdecimal() for v in cpu)
        and 0 < int(cpu[0]) * 10 <= int(cpu[1]),
        "Source capture cgroup limits differ",
    )
    events = dict(line.split() for line in (cg / "memory.events").read_text().splitlines())
    need(
        all(events.get(key) == "0" for key in ("max", "oom", "oom_kill")),
        "Source capture memory pressure",
    )


def _source_resources(output_parent):
    """Fail closed unless the existing Source host, volume and limits are admitted."""
    need(os.geteuid() == 0, "Root source capture required")
    need(
        all(os.environ.get(name) == "false" for name in READ_FLAGS),
        "Explicit job-private deletion and retention flags required",
    )
    _cgroup_limits()
    available = next(
        int(line.split()[1]) * 1024
        for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith("MemAvailable:")
    )
    need(
        available >= 704 * 1024**2 and os.getloadavg()[0] <= 2.0,
        "Source receiver headroom unavailable",
    )
    parent = Path(output_parent)
    need(
        parent.is_absolute()
        and parent.resolve() == parent
        and parent.stat().st_uid == 0
        and stat.S_IMODE(parent.stat().st_mode) == 0o700,
        "Private source scratch parent required",
    )
    need(
        SOURCE_VOLUME in parent.parents and parent.stat().st_dev == SOURCE_DEVICE,
        "Exact existing source volume required",
    )
    usage = shutil.disk_usage(parent)
    need(
        usage.free >= CAPTURE_BYTES + max(2 * 1024**3, (usage.total + 19) // 20),
        "Source capture reserve unavailable",
    )


def _engine_binding(engine, database):
    """Controller must strip all unreviewed URL query options before constructing engine."""
    url = engine.url
    need(
        url.drivername == "postgresql+psycopg2"
        and url.host == "127.0.0.1"
        and url.port == 5432
        and url.database == database
        and url.username == "greenmind_archive_reader_33fe395"
        and bool(url.password)
        and not url.query,
        "Exact query-free local reader connection required",
    )


def _identity(db, database, database_oid):
    data = dict(
        db.execute(
            text("""SELECT current_database() AS database,
      (SELECT oid::bigint FROM pg_database WHERE datname=current_database()) AS database_oid,
      current_user AS role,session_user AS session_role,
      (SELECT oid::bigint FROM pg_roles WHERE rolname=current_user) AS role_oid,
      (SELECT oid::bigint FROM pg_roles WHERE rolname=session_user) AS session_oid,
      (SELECT rolcanlogin AND NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls) FROM pg_roles WHERE rolname=current_user) AS role_unprivileged,
      current_setting('server_version_num')::int AS server_version_num,
      current_setting('transaction_read_only') AS transaction_read_only,
      pg_is_in_recovery() AS in_recovery,inet_server_addr()::text AS server_address,
      inet_server_port() AS server_port,pg_postmaster_start_time()::text AS postmaster_start""")
        )
        .mappings()
        .one()
    )
    need(
        data["database"] == database
        and data["database_oid"] == database_oid
        and data["role"] == "greenmind_archive_reader_33fe395"
        and data["session_role"] == data["role"]
        and data["role_oid"] == 499352
        and data["session_oid"] == 499352
        and data["role_unprivileged"] is True
        and data["server_version_num"] == 150010
        and data["transaction_read_only"] == "on"
        and data["in_recovery"] is False
        and data["postmaster_start"] == "2026-08-31 16:13:00.505241+00"
        and data["server_port"] == 5432
        and data["server_address"] == "172.28.20.5/32",
        "Exact readonly PG15 source and reader required",
    )
    return data


def _guards(output, lease_deadline, checkpoint):
    need(
        callable(checkpoint)
        and type(lease_deadline) in (int, float)
        and time.monotonic() < lease_deadline <= time.monotonic() + 110,
        "Current bounded source lease required",
    )
    _source_resources(output.parent)
    end = min(time.monotonic() + 110, lease_deadline)

    def check():
        need(time.monotonic() < end, "Source snapshot lease or deadline expired")
        checkpoint()
        _cgroup_limits()
        available = next(
            int(line.split()[1]) * 1024
            for line in Path("/proc/meminfo").read_text().splitlines()
            if line.startswith("MemAvailable:")
        )
        need(available >= 704 * 1024**2 and os.getloadavg()[0] <= 2.0, "Source resource pressure")
        usage = shutil.disk_usage(output.parent)
        need(
            usage.free >= max(2 * 1024**3, (usage.total + 19) // 20), "Source disk reserve breached"
        )
        need(time.monotonic() < end, "Source snapshot lease or deadline expired")

    return check


def _new_private_dir(path):
    need(not os.path.lexists(path), "New source capture directory required")
    path.mkdir(mode=0o700)
    need(
        stat.S_IMODE(path.stat().st_mode) == 0o700 and path.stat().st_uid == 0,
        "Root private capture directory required",
    )


def _complete(path, proof):
    raw = (
        json.dumps(proof, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    with os.fdopen(fd, "wb") as f:
        os.fchmod(f.fileno(), 0o400)
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return hashlib.sha256(raw).hexdigest()


def capture_gateway(engine, output, *, lease_deadline, checkpoint, counter_factory=FrameCounter):
    """Capture 62+10 native columns in one RR snapshot, or leave no completion proof.

    checkpoint must revalidate the existing source lease; lease_deadline is its
    actual monotonic expiry, not an independently invented 110-second budget.
    Any non-default counter_factory requires a separately pinned and tested
    implementation, verified by the invoking controller before this call.
    """
    output = Path(output)
    columns = roster()
    _engine_binding(engine, "plantdb")
    check = _guards(output, lease_deadline, checkpoint)
    _new_private_dir(output)
    started = datetime.now(UTC).isoformat()
    capture_id = uuid.uuid4().hex
    entries = []
    used = 0
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as db, db.begin():
        db.execute(text("SET TRANSACTION READ ONLY"))
        db.execute(text("SET LOCAL statement_timeout='8000ms'"))
        db.execute(text("SET LOCAL lock_timeout='150ms'"))
        # Fence both physical tables against concurrent DDL for the whole snapshot.
        db.execute(
            text("LOCK TABLE public.wav_feature, public.wav_feature_version IN ACCESS SHARE MODE")
        )
        identity = _identity(db, "plantdb", 16384)
        need(
            db.execute(text(SCOPE_SQL)).scalar_one() == SOURCE_SCOPE,
            "Fresh same-snapshot scientific catalog differs",
        )
        snapshot = db.execute(text("SELECT pg_current_snapshot()::text")).scalar_one()
        snapshot_sha = hashlib.sha256(snapshot.encode()).hexdigest()
        cursor = db.connection.driver_connection.cursor()
        try:
            for table in TABLES:
                check()
                fields = [x["name"] for x in columns[table]]
                path = output / (table + ".pgcopy")
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as body:
                    sink = FramedSink(
                        body, check, CAPTURE_BYTES - used, len(fields), counter_factory
                    )
                    rows = catalog_capture.copy_gateway_blocks(
                        db, cursor, table, fields, sink, body, check, pause_seconds=0.25
                    )
                    counted = sink.counter.finish()
                    need(
                        type(counted) is int and rows == counted,
                        "Native row counter disagrees with PostgreSQL COPY",
                    )
                    body.flush()
                    os.fsync(body.fileno())
                    os.fchmod(body.fileno(), 0o400)
                    os.fsync(body.fileno())
                used += sink.written
                entries.append(
                    {
                        "table": table,
                        "presence": "present_nonempty" if rows else "present_empty",
                        "file": path.name,
                        "rows": rows,
                        "bytes": sink.written,
                        "sha256": sink.digest.hexdigest(),
                        "columns": columns[table],
                        "capture_order": "physical_heap_ranges",
                        "identity_key": ["id"],
                    }
                )
            check()
            need(
                db.execute(text(SCOPE_SQL)).scalar_one() == SOURCE_SCOPE
                and _identity(db, "plantdb", 16384) == identity,
                "Scientific catalog or source changed during capture",
            )
        finally:
            cursor.close()
    check()
    proof = {
        "format": FORMAT,
        "capture_id": capture_id,
        "source": "gateway",
        "complete": True,
        "source_read_only": True,
        "started_at": started,
        "finished_at": datetime.now(UTC).isoformat(),
        "source_identity": identity,
        "snapshot_sha256": snapshot_sha,
        "scope_sha256": SOURCE_SCOPE_SHA256,
        "scope": SOURCE_SCOPE,
        "files": entries,
        "total_bytes": used,
        "cross_database_atomic": False,
        "global_parent_fk_closure_proven": False,
    }
    return {"manifest": proof, "manifest_sha256": _complete(output / "capture.json", proof)}


def observe_direct_feature_absence(engine, output, *, checkpoint, lease_deadline):
    """Independent, resource-bounded Direct RR absence evidence; no empty-row fiction."""
    output = Path(output)
    _engine_binding(engine, "greenmind_direct_production")
    check = _guards(output, lease_deadline, checkpoint)
    _new_private_dir(output)
    check()
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as db, db.begin():
        db.execute(text("SET TRANSACTION READ ONLY"))
        db.execute(text("SET LOCAL statement_timeout='8000ms'"))
        db.execute(text("SET LOCAL lock_timeout='150ms'"))
        identity = _identity(db, "greenmind_direct_production", 301614)
        names = ("direct_feature_result", "direct_feature_job")
        present = {
            name: db.execute(
                text("SELECT to_regclass(:name) IS NOT NULL"), {"name": "public." + name}
            ).scalar_one()
            for name in names
        }
        need(not any(present.values()), "Direct scientific bootstrap catalog changed")
        snapshot = db.execute(text("SELECT pg_current_snapshot()::text")).scalar_one()
        check()
        need(
            _identity(db, "greenmind_direct_production", 301614) == identity,
            "Direct source changed",
        )
        need(
            all(
                not db.execute(
                    text("SELECT to_regclass(:name) IS NOT NULL"), {"name": "public." + name}
                ).scalar_one()
                for name in names
            ),
            "Direct scientific bootstrap catalog changed during capture",
        )
    check()
    proof = {
        "format": "greenmind.science-direct-absence.v3",
        "capture_id": uuid.uuid4().hex,
        "source": "direct",
        "identity": identity,
        "snapshot_sha256": hashlib.sha256(snapshot.encode()).hexdigest(),
        "tables": {name: "absent" for name in names},
        "complete": True,
        "cross_database_atomic": False,
        "prior_direct_data_captured": False,
    }
    return {"manifest": proof, "manifest_sha256": _complete(output / "absence.json", proof)}
