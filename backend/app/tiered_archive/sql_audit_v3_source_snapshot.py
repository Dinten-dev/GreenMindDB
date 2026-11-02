"""Source-root import of an exported RR snapshot; encrypted response only."""

import base64
import hashlib
import json
import os
import re
import socket
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from app.raw_archive.policy import ArchiveBlocked

from . import sql_audit_v3_backup


def need(value, reason):
    if not value:
        raise ArchiveBlocked(reason)


class Result:
    def __init__(self, cursor):
        self.cursor = cursor

    def all(self):
        rows = self.cursor.fetchall()
        self.cursor.close()
        return rows

    def one(self):
        rows = self.all()
        need(len(rows) == 1, "Exact Source audit row required")
        return rows[0]

    def one_or_none(self):
        rows = self.all()
        need(len(rows) <= 1, "Unique Source audit row required")
        return rows[0] if rows else None

    def scalar_one(self):
        return self.one()[0]

    def scalar_one_or_none(self):
        row = self.one_or_none()
        return None if row is None else row[0]

    def scalars(self):
        owner = self
        class Scalars:
            def all(self):
                return [row[0] for row in owner.all()]
        return Scalars()

    def mappings(self):
        owner = self
        names = [item.name for item in self.cursor.description]
        class Mappings:
            def one(self):
                return dict(zip(names, owner.one(), strict=True))
            def one_or_none(self):
                row = owner.one_or_none()
                return None if row is None else dict(zip(names, row, strict=True))
        return Mappings()


class ImportedSnapshotDB:
    """SQLAlchemy text compilation without opening, rolling back or replacing DBAPI tx."""
    dialect = SimpleNamespace(name="postgresql")

    def __init__(self, connection):
        from sqlalchemy.dialects import postgresql
        self.connection = connection
        self._dialect = postgresql.dialect(paramstyle="pyformat")

    def execute(self, statement, params=None):
        cursor = self.connection.cursor()
        try:
            cursor.execute(str(statement.compile(dialect=self._dialect)), params or {})
            if cursor.description is None:
                cursor.close()
                return None
            return Result(cursor)
        except BaseException:
            cursor.close()
            raise


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def aad(request, source_public):
    return canonical({"format": "greenmind.sql-v3-snapshot-cipher.v1",
                      "request_sha256": hashlib.sha256(canonical(request)).hexdigest(), "source_public": source_public})


SCHEMA_BYTES = 2 * 1024 * 1024


def schema_dump(snapshot, password, client_dir, client_sha256, *, checkpoint):
    """Capture only a bounded custom-format schema in memory, in this cgroup."""
    import selectors
    import subprocess

    from .backup import client_command

    need(type(client_dir) is str and Path(client_dir).is_absolute() and type(client_sha256) is str
         and re.fullmatch(r"[a-f0-9]{64}", client_sha256), "Pinned Source PG client required")
    client_path = Path(client_dir)
    need(client_path.resolve() == client_path, "Exact Source PG client path required")
    for parent in client_path.parents:
        info = parent.lstat()
        need(info.st_uid == 0 and not info.st_mode & 0o022, "Source PG client parent differs")
    old_dir = os.environ.get("TIERED_ARCHIVE_PG_CLIENT_DIR")
    old_sha = os.environ.get("TIERED_ARCHIVE_PG_CLIENT_MANIFEST_SHA256")
    try:
        os.environ["TIERED_ARCHIVE_PG_CLIENT_DIR"] = client_dir
        os.environ["TIERED_ARCHIVE_PG_CLIENT_MANIFEST_SHA256"] = client_sha256
        command = client_command("pg_dump")
    finally:
        for name, value in (("TIERED_ARCHIVE_PG_CLIENT_DIR", old_dir), ("TIERED_ARCHIVE_PG_CLIENT_MANIFEST_SHA256", old_sha)):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    command += ["--no-password", "--format=custom", "--schema-only", "--no-owner", "--no-acl", "--no-blobs", "--snapshot=" + snapshot, "--dbname=plantdb"]
    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PGHOST": "127.0.0.1", "PGPORT": "5432", "PGUSER": "admin", "PGPASSWORD": password, "PGCONNECT_TIMEOUT": "3", "PGOPTIONS": "-c statement_timeout=8000 -c lock_timeout=150"}
    data = bytearray()
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env) as child:  # noqa: S603 -- fixed, pinned executable and argv
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                while True:
                    checkpoint()
                    if selector.select(timeout=0.2):
                        block = os.read(child.stdout.fileno(), min(65536, SCHEMA_BYTES - len(data) + 1))
                        if not block:
                            break
                        data.extend(block)
                        need(len(data) <= SCHEMA_BYTES, "Source schema exceeds bounded transport")
                    elif child.poll() is not None:
                        continue
            need(child.wait(timeout=3) == 0 and data.startswith(b"PGDMP"), "Source schema dump failed")
            checkpoint()
            return bytes(data)
        except BaseException:
            if child.poll() is None:
                child.kill()
            child.wait()
            raise
        finally:
            env.pop("PGPASSWORD", None)


def encrypt_payload(payload, request, *, schema=None):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    sql_audit_v3_backup.validate(payload)
    if schema is None:
        raw = sql_audit_v3_backup.encode(payload)
    else:
        need(type(schema) is bytes and schema.startswith(b"PGDMP") and len(schema) <= SCHEMA_BYTES, "Bounded Source schema required")
        raw = canonical({"format": "greenmind.sql-v3-audit-schema.v1", "audit": payload, "schema": base64.b64encode(schema).decode()})
    archive_public = X25519PublicKey.from_public_bytes(bytes.fromhex(request["archive_public"]))
    private = X25519PrivateKey.generate()
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    associated = aad(request, public)
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=bytes.fromhex(request["nonce"]), info=b"greenmind-sql-v3-source-snapshot-v1").derive(private.exchange(archive_public))
    iv = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(iv, raw, associated)
    return {"format": "greenmind.sql-v3-snapshot-cipher.v1", "lease_session": request["lease_session"],
            "snapshot_sha256": hashlib.sha256(request["snapshot"].encode()).hexdigest(), "nonce": request["nonce"],
            "source_public": public, "iv": iv.hex(), "ciphertext": base64.b64encode(ciphertext).decode(),
            "plaintext_sha256": hashlib.sha256(raw).hexdigest()}


def import_snapshot(connection, request, *, checkpoint):
    """First importer statement binds the caller's still-exported RR snapshot."""
    from psycopg2.extensions import ISOLATION_LEVEL_REPEATABLE_READ

    from .sql_deletion_guard import _identity

    checkpoint()
    connection.set_session(isolation_level=ISOLATION_LEVEL_REPEATABLE_READ, readonly=True, autocommit=False)
    checkpoint()
    with connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION SNAPSHOT %s", (request["snapshot"],))
    checkpoint()
    db = ImportedSnapshotDB(connection)
    actual = _identity(db)
    need(actual == request["dba_identity"] and all(actual.get(k) == request["reader_identity"].get(k) and actual.get(k) is not None
         for k in ("database", "database_oid", "server_address", "server_port", "postmaster_start")), "Imported snapshot physical Source differs")
    return sql_audit_v3_backup.snapshot_payload(db, request["snapshot"], source_identity=actual,
                schema_sha256=request["schema_sha256"], epoch=request["epoch"], genesis_sha256=request["genesis_sha256"], checkpoint=checkpoint)


def capture(request, binding, *, checkpoint):
    """Source credentials remain here; no audit plaintext returned outside ciphertext."""
    import psycopg2

    from app.raw_archive import scientific_capture, scientific_scope_control

    from .sql_audit_v3_crypto import MANIFEST_SHA256
    from .sql_audit_v3_source import _observe_container

    need(os.geteuid() == 0 and os.getenv("TIERED_ARCHIVE_SQL_V3_SOURCE_SNAPSHOT_ENABLED") == "true" and callable(checkpoint), "Explicit Source-root V3 snapshot required")
    keys = {"format", "snapshot", "lease_session", "nonce", "archive_public", "binding_sha256", "dba_identity", "reader_identity", "schema_sha256", "epoch", "genesis_sha256", "expires_at", "producer_sha256", "source_manifest_sha256", "schema_scope_sha256", "pg_client_dir", "pg_client_manifest_sha256", "crypto_runtime_dir", "crypto_manifest_sha256"}
    need(type(request) is dict and set(request) == keys and request["format"] == "greenmind.sql-v3-source-snapshot-request.v1"
         and type(request["snapshot"]) is str and re.fullmatch(r"[A-Fa-f0-9-]{3,128}", request["snapshot"])
         and type(request["lease_session"]) is str and re.fullmatch(r"[a-f0-9]{32}", request["lease_session"])
         and all(type(request[k]) is str and re.fullmatch(r"[a-f0-9]{64}", request[k]) for k in ("nonce", "archive_public", "binding_sha256", "schema_sha256", "genesis_sha256", "producer_sha256", "source_manifest_sha256"))
         and type(request["epoch"]) is str and re.fullmatch(r"[a-f0-9]{32}", request["epoch"])
         and type(request["dba_identity"]) is dict and type(request["reader_identity"]) is dict
         and all(type(request[k]) is str and re.fullmatch(r"[a-f0-9]{64}", request[k]) for k in ("schema_scope_sha256", "pg_client_manifest_sha256", "crypto_manifest_sha256"))
         and type(request["pg_client_dir"]) is str and Path(request["pg_client_dir"]).is_absolute()
         and type(request["crypto_runtime_dir"]) is str and Path(request["crypto_runtime_dir"]).is_absolute()
         and request["crypto_manifest_sha256"] == MANIFEST_SHA256
         and request["pg_client_manifest_sha256"] == "55bb10b9c0cac4be01ef2d14ca03e51e3ccd837fb7ec83c2a46d4359d4b0e6f0"
         and type(binding) is dict and hashlib.sha256(canonical(binding)).hexdigest() == request["binding_sha256"], "Exact Source snapshot challenge required")
    root = Path(__file__).resolve().parents[3]
    manifest_path = root / "manifest.json"
    manifest = scientific_scope_control._private_json(manifest_path, request["source_manifest_sha256"])
    from .ledger import _private_read
    from .restore_inputs import frozen
    producer = Path(__file__).resolve()
    first = frozen(producer)
    raw_producer = _private_read(producer, 1024 * 1024)
    need(frozen(producer) == first and hashlib.sha256(raw_producer).hexdigest() == request["producer_sha256"]
         and manifest.get("files", {}).get("backend/app/tiered_archive/sql_audit_v3_source_snapshot.py") == request["producer_sha256"],
         "Source snapshot producer manifest differs")
    from app.raw_archive.snapshot_api import check_loaded_function_code

    from . import backup, restore_fence, sql_audit_v3, sql_deletion_guard

    for module, names in (
        (sys.modules[__name__], ("capture", "import_snapshot", "ImportedSnapshotDB.execute", "schema_dump", "encrypt_payload", "aad", "canonical")),
        (sql_audit_v3_backup, ("snapshot_payload", "capture_structure", "validate", "encode")),
        (sql_audit_v3, ("schema_sha256", "inspect_writer")),
        (sql_deletion_guard, ("_identity",)),
        (backup, ("client_command",)),
        (restore_fence, ("_relation_pin",)),
    ):
        path = Path(module.__file__).resolve()
        relative = path.relative_to(root).as_posix()
        before = frozen(path)
        source = _private_read(path, 1024 * 1024)
        need(frozen(path) == before and hashlib.sha256(source).hexdigest() == manifest.get("files", {}).get(relative), "Source snapshot dependency changed")
        check_loaded_function_code(module, path, source, names)
    try:
        expires = datetime.fromisoformat(request["expires_at"])
        remaining = (expires - datetime.now(UTC)).total_seconds()
    except (TypeError, ValueError):
        remaining = -1
    need(0 < remaining <= 45, "Source snapshot challenge expired")
    locked = scientific_scope_control._source_lock(full=True)
    need(locked["device"] == binding.get("lock_device") and locked["inode"] == binding.get("lock_inode")
         and locked["uid"] == binding.get("lease_uid") and locked["command_sha256"] == binding.get("lease_command_sha256")
         and locked["executable_sha256"] == binding.get("lease_executable_sha256") and socket.gethostname() == binding.get("hostname")
         and hashlib.sha256(Path("/etc/machine-id").read_bytes()).hexdigest() == binding.get("machine_sha256")
         and Path("/proc/sys/kernel/random/boot_id").read_text().strip() == binding.get("boot_id"), "Source snapshot lease binding differs")
    ticks = os.sysconf("SC_CLK_TCK")
    need(type(ticks) is int and ticks > 0, "Source clock unavailable")
    deadline = min(time.monotonic() + 45, locked["start_ticks"] / ticks + 110)
    holder = {k: locked[k] for k in ("device", "inode", "pid", "uid", "start_ticks")}
    def check():
        checkpoint()
        scientific_capture._cgroup_limits()
        need(time.monotonic() < deadline and datetime.now(UTC) < expires
             and scientific_scope_control._source_lock(full=False) == holder, "Source snapshot lease expired or changed")
    check()
    generation, password = _observe_container()
    conn = None
    try:
        conn = psycopg2.connect(host="127.0.0.1", port=5432, dbname="plantdb", user="admin", password=password,
                                connect_timeout=3, options="-c statement_timeout=8000 -c lock_timeout=150 -c search_path=pg_catalog,public")
        payload = import_snapshot(conn, request, checkpoint=check)
        schema = schema_dump(request["snapshot"], password, request["pg_client_dir"], request["pg_client_manifest_sha256"], checkpoint=check)
        check()
        conn.rollback()
        after, _ = _observe_container()
        need(after == generation, "Source database generation changed")
        check()
        return encrypt_payload(payload, request, schema=schema)
    except Exception:
        raise ArchiveBlocked("Source snapshot unavailable or requires reconciliation") from None
    finally:
        if conn is not None:
            conn.close()
        password = None
