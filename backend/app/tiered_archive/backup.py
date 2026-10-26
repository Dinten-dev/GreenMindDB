"""Consistent schema/master-data backups encrypted before leaving the worker."""

import os
import secrets
import stat
import struct
import subprocess
import time

from app.raw_archive.policy import ArchiveBlocked, checksum

MAGIC = b"GMDBBACKUP1\0"
BLOCK_BYTES = 1024 * 1024
MAX_BYTES = 64 * 1024**2
MASTER_TABLES = ("organization", "users", "zone", "gateway", "sensor", "plant")
DIRECT_MASTER_TABLES = ("direct_device", "direct_enrollment", "direct_session")


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


def key_from_file(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as body:
        info = os.fstat(body.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid not in {0, os.getuid()}
            or info.st_mode & 0o077
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


def dump(database_url, source, output, *, checkpoint, executable="/usr/bin/pg_dump"):
    """One exported snapshot spans schema and allowlisted master rows."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

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
    tables = MASTER_TABLES if source == "gateway" else DIRECT_MASTER_TABLES
    engine = create_engine(database_url, pool_size=1, max_overflow=0)
    try:
        with (
            engine.connect().execution_options(isolation_level="REPEATABLE READ") as db,
            db.begin(),
        ):
            db.execute(text("SET TRANSACTION READ ONLY"))
            snapshot = db.execute(text("SELECT pg_export_snapshot()")).scalar_one()
            outputs = []
            for label, args in [
                ("schema", ["--schema-only"]),
                (
                    "masters",
                    ["--data-only", *[flag for table in tables for flag in ("--table", table)]],
                ),
            ]:
                target = output / (label + ".dump")
                command = [
                    executable,
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
            return outputs
    finally:
        engine.dispose()
