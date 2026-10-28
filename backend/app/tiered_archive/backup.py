"""Consistent schema/master-data backups encrypted before leaving the worker."""

import hashlib
import json
import os
import secrets
import stat
import struct
import subprocess
import time
from pathlib import Path

from app.raw_archive.policy import ArchiveBlocked, checksum

MAGIC = b"GMDBBACKUP1\0"
BLOCK_BYTES = 1024 * 1024
MAX_BYTES = 64 * 1024**2
# Older proofs remain file-restorable, but cannot prove today's authority scope:
# v1 omitted access grants/assignments; v2 omitted sessions and invitations.
MASTER_PROOF_VERSION = 3
LEGACY_MASTER_TABLES_V2 = (
    "alembic_version",
    "organization",
    "users",
    "zone",
    "gateway",
    "sensor",
    "plant",
    "zone_access",
    "plant_sensor_assignment",
    "plant_observation_access",
)
MASTER_TABLES = (
    *LEGACY_MASTER_TABLES_V2,
    "form_submission",
    "access_invitation",
    "auth_session",
)
DIRECT_MASTER_TABLES = ("direct_device", "direct_enrollment", "direct_session")


def master_tables(source, *, proof_version=MASTER_PROOF_VERSION):
    if source not in {"gateway", "direct"}:
        raise ArchiveBlocked("Unknown backup source")
    if type(proof_version) is not int or proof_version not in {2, 3}:
        raise ArchiveBlocked("Unsupported master-data proof version")
    if source == "direct":
        return DIRECT_MASTER_TABLES
    return LEGACY_MASTER_TABLES_V2 if proof_version == 2 else MASTER_TABLES


def check_master_tables(db, source, *, checkpoint, proof_version=MASTER_PROOF_VERSION):
    """Fail before dumping if the explicit recovery scope is incomplete."""
    from sqlalchemy import inspect, text

    names = master_tables(source, proof_version=proof_version)
    inspector = inspect(db)
    schema = "public" if db.dialect.name == "postgresql" else None
    present = set(inspector.get_table_names(schema=schema))
    for name in names:
        checkpoint()
        if name not in present:
            raise ArchiveBlocked("Required master-data table missing: " + name)
        if (
            schema
            and not db.execute(
                text("SELECT has_table_privilege(current_user, :table, 'SELECT')"),
                {"table": "public." + name},
            ).scalar_one()
        ):
            raise ArchiveBlocked("Required master-data SELECT permission missing: " + name)
        # A new FK outside the fixed scope needs an explicit policy update.
        for fk in inspector.get_foreign_keys(name, schema=schema):
            if fk["referred_table"] not in names or fk.get("referred_schema") not in (None, schema):
                raise ArchiveBlocked("Master-data dependency outside backup scope: " + name)
    return names


def client_command(name):
    """Use a pinned native client bundle; every child remains in the job cgroup."""
    import re

    if name not in {"pg_dump", "pg_restore", "psql"}:
        raise ArchiveBlocked("Unknown PostgreSQL client")
    root = os.environ.get("TIERED_ARCHIVE_PG_CLIENT_DIR")
    if not root:
        return ["/usr/bin/" + name]
    root = Path(root)
    pinned = os.environ.get("TIERED_ARCHIVE_PG_CLIENT_MANIFEST_SHA256", "")
    if not root.is_absolute() or root.resolve() != root or not re.fullmatch("[a-f0-9]{64}", pinned):
        raise ArchiveBlocked("Pinned absolute PostgreSQL client runtime required")
    manifest_path = root / "manifest.json"
    if manifest_path.stat().st_size > 65536 or checksum(manifest_path) != pinned:
        raise ArchiveBlocked("PostgreSQL client runtime manifest differs")
    manifest = json.loads(manifest_path.read_text())
    files = manifest.get("files", {})
    if manifest.get("version") != 1 or not 4 <= len(files) <= 64:
        raise ArchiveBlocked("Incomplete PostgreSQL client runtime")
    for relative, expected in files.items():
        part = Path(relative)
        if part.is_absolute() or ".." in part.parts:
            raise ArchiveBlocked("Unsafe PostgreSQL client runtime path")
        path = root / part
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_mode & 0o222
            or checksum(path) != expected
        ):
            raise ArchiveBlocked("PostgreSQL client runtime file differs")
    for path in [root, manifest_path]:
        info = path.lstat()
        if info.st_uid != 0 or info.st_mode & 0o222 or path.is_symlink():
            raise ArchiveBlocked("PostgreSQL client runtime must be root-owned and read-only")
    loader = manifest.get("loader")
    if loader not in {"lib/ld-linux-x86-64.so.2", "lib/ld-musl-x86_64.so.1"}:
        raise ArchiveBlocked("Unknown PostgreSQL client loader")
    binary = "bin/" + name
    if loader not in files or binary not in files:
        raise ArchiveBlocked("Missing pinned PostgreSQL executable")
    return [str(root / loader), "--library-path", str(root / "lib"), str(root / binary)]


def require_current(receipts, *, source, environment, destination, now):
    from datetime import timedelta

    from .policy import POLICY_SHA256, utc

    for receipt in receipts:
        origin = receipt["source"]
        if receipt["state"] != "verified" or origin["dataset"] != "database_backup":
            continue
        if (
            origin["source"] != source
            or origin["environment"] != environment
            or receipt.get("destination") != destination
            or receipt.get("policy") != POLICY_SHA256
        ):
            continue
        files = receipt.get("files", [])
        if (
            not files
            or files[0].get("backup_format") not in (None, "schema-master-v2")
            or type(files[0].get("master_proof_version")) is not int
            or files[0]["master_proof_version"] != MASTER_PROOF_VERSION
        ):
            continue
        snapshot = receipt.get("snapshot", {})
        if not snapshot.get("provider_id") or not snapshot.get("box_id"):
            continue
        checked, created, captured = (
            utc(receipt["verified_at"]),
            utc(snapshot["created_at"]),
            utc(origin["end"]),
        )
        if (
            captured <= created <= checked <= now
            and now - captured <= timedelta(hours=24)
            and now - created <= timedelta(hours=24)
        ):
            return receipt
    raise ArchiveBlocked("Current snapshot-verified schema and master-data backup required")


def private_key_permissions(fd, info):
    """Accept private modes or systemd's root + current-user read-only ACL."""
    mode = stat.S_IMODE(info.st_mode)
    if not mode & 0o077:
        return True
    # systemd LoadCredential uses an ACL for the service uid. Its group-mode
    # bits describe the ACL mask, not access for the file's owning group.
    # Accept only the observed private shape, never generic group-readable keys.
    if mode != 0o440 or info.st_uid != 0 or info.st_gid != 0:
        return False
    undefined = 0xFFFFFFFF
    expected = struct.pack("<I", 2) + b"".join(
        struct.pack("<HHI", tag, permission, identity)
        for tag, permission, identity in (
            (1, 4, undefined),  # owner root: read
            (2, 4, os.getuid()),  # current service user: read
            (4, 0, undefined),  # owning group: no access
            (16, 4, undefined),  # ACL mask: read only
            (32, 0, undefined),  # everyone else: no access
        )
    )
    try:
        return os.getxattr(fd, "system.posix_acl_access") == expected
    except (AttributeError, OSError):
        return False


def key_from_file(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as body:
        info = os.fstat(body.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid not in {0, os.getuid()}
            or not private_key_permissions(body.fileno(), info)
        ):
            raise ArchiveBlocked("Backup encryption key must be private")
        key = body.read(33)
    if len(key) != 32:
        raise ArchiveBlocked("Backup key must contain exactly 32 random bytes")
    return key


def encrypt(source, target, key, *, checkpoint):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = secrets.token_bytes(8)
    cipher = AESGCM(key)
    with source.open("rb") as original, target.open("xb") as out:
        os.chmod(target, 0o600)
        header = MAGIC + nonce
        out.write(header)
        number = 0
        while body := original.read(BLOCK_BYTES):
            checkpoint()
            sealed = cipher.encrypt(
                nonce + struct.pack(">I", number), body, header + struct.pack(">I", number)
            )
            out.write(struct.pack(">I", len(sealed)) + sealed)
            number += 1
            if out.tell() > MAX_BYTES:
                raise ArchiveBlocked("Encrypted backup exceeds bounded part budget")
        # Authenticated EOF prevents an attacker accepting a truncated prefix.
        out.write(
            struct.pack(">I", 16)
            + cipher.encrypt(
                nonce + struct.pack(">I", number), b"", header + struct.pack(">I", number)
            )
        )
        out.flush()
        os.fsync(out.fileno())
    return {
        "format": "bin",
        "sha256": checksum(target),
        "bytes": target.stat().st_size,
        "plaintext_sha256": checksum(source),
    }


def decrypt(source, target, key, expected_sha256, *, checkpoint):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    cipher = AESGCM(key)
    with source.open("rb") as sealed, target.open("xb") as out:
        os.chmod(target, 0o600)
        header = sealed.read(len(MAGIC) + 8)
        if len(header) != len(MAGIC) + 8 or not header.startswith(MAGIC):
            raise ArchiveBlocked("Unknown encrypted backup version")
        nonce = header[-8:]
        number = 0
        while True:
            checkpoint()
            raw = sealed.read(4)
            if len(raw) != 4:
                raise ArchiveBlocked("Truncated encrypted backup")
            size = struct.unpack(">I", raw)[0]
            if not 16 <= size <= BLOCK_BYTES + 16:
                raise ArchiveBlocked("Encrypted record exceeds budget")
            body = sealed.read(size)
            try:
                plain = cipher.decrypt(
                    nonce + struct.pack(">I", number), body, header + struct.pack(">I", number)
                )
            except Exception as error:
                raise ArchiveBlocked("Backup authentication failed") from error
            if not plain:
                if sealed.read(1):
                    raise ArchiveBlocked("Trailing data after authenticated EOF")
                break
            out.write(plain)
            number += 1
            if out.tell() > MAX_BYTES:
                raise ArchiveBlocked("Decrypted backup exceeds budget")
        out.flush()
        os.fsync(out.fileno())
    if checksum(target) != expected_sha256:
        raise ArchiveBlocked("Restored database backup checksum mismatch")


def master_proof(db, source, *, checkpoint, proof_version=MASTER_PROOF_VERSION):
    """Bounded typed readback in the caller's consistent database transaction."""
    from sqlalchemy import MetaData, Table, select, text

    from .codec import encode

    tables = check_master_tables(db, source, checkpoint=checkpoint, proof_version=proof_version)
    if db.dialect.name == "postgresql":
        db.execute(text("SET LOCAL TIME ZONE 'UTC'"))
    result = {}
    total = 0
    for name in tables:
        checkpoint()
        table = Table(
            name,
            MetaData(),
            schema="public" if db.dialect.name == "postgresql" else None,
            autoload_with=db,
        )
        keys = list(table.primary_key.columns)
        if not keys:
            raise ArchiveBlocked("Master-data proof requires stable primary keys")
        schema = {
            "columns": [
                {
                    "name": col.name,
                    "type": col.type.compile(dialect=db.dialect),
                    "nullable": col.nullable,
                }
                for col in table.columns
            ],
            "primary_key": [col.name for col in keys],
        }
        digest = hashlib.sha256(encode(schema))
        count = 0
        query = select(table).order_by(*keys).execution_options(stream_results=True, yield_per=128)
        with db.execute(query) as rows:
            for row in rows.mappings():
                checkpoint()
                raw = encode(dict(row))
                total += len(raw)
                if total > MAX_BYTES:
                    raise ArchiveBlocked("Master-data proof exceeds bounded backup budget")
                digest.update(len(raw).to_bytes(8, "big"))
                digest.update(raw)
                count += 1
        result[name] = {"rows": count, "content_sha256": digest.hexdigest()}
    return {"version": proof_version, "tables": result}


def dump(database_url, source, output, *, checkpoint, executable=None, with_proof=False):
    """One exported snapshot spans schema and allowlisted master rows."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    tables = master_tables(source)
    url = make_url(database_url)
    env = {
        **os.environ,
        "PGHOST": url.host or "localhost",
        "PGPORT": str(url.port or 5432),
        "PGDATABASE": url.database,
        "PGUSER": url.username,
        "PGPASSWORD": url.password or "",
        "PGCONNECT_TIMEOUT": "3",
        "PGOPTIONS": "-c statement_timeout=8000 -c lock_timeout=150",
    }
    engine = create_engine(database_url, pool_size=1, max_overflow=0)
    try:
        with (
            engine.connect().execution_options(isolation_level="REPEATABLE READ") as db,
            db.begin(),
        ):
            db.execute(text("SET TRANSACTION READ ONLY"))
            check_master_tables(db, source, checkpoint=checkpoint)
            snapshot = db.execute(text("SELECT pg_export_snapshot()")).scalar_one()
            outputs = []
            for label, args in [
                ("schema", ["--schema-only"]),
                (
                    "masters",
                    [
                        "--data-only",
                        *[flag for table in tables for flag in ("--table", "public." + table)],
                    ],
                ),
            ]:
                target = output / (label + ".dump")
                command = [
                    *([executable] if executable else client_command("pg_dump")),
                    "--no-password",
                    "--format=custom",
                    "--no-owner",
                    "--no-acl",
                    "--snapshot",
                    snapshot,
                    "--file",
                    str(target),
                    *args,
                ]
                started = time.monotonic()
                with subprocess.Popen(  # noqa: S603 -- fixed pg_dump executable and validated argv
                    command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                ) as process:
                    try:
                        while process.poll() is None:
                            checkpoint()
                            if time.monotonic() - started > 120 or (
                                target.exists() and target.stat().st_size > MAX_BYTES
                            ):
                                raise ArchiveBlocked("Database backup resource budget exceeded")
                            time.sleep(2)
                        if process.returncode:
                            raise ArchiveBlocked("Consistent database dump failed")
                    except BaseException:
                        process.kill()
                        process.wait()
                        raise
                os.chmod(target, 0o600)
                outputs.append(target)
            if with_proof:
                return outputs, master_proof(db, source, checkpoint=checkpoint)
            return outputs
    finally:
        engine.dispose()
