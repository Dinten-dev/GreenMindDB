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
AUTH_MAX_BYTES = 256 * 1024
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


def master_tables(source, *, proof_version=MASTER_PROOF_VERSION, schema_scope=None):
    if source not in {"gateway", "direct"}:
        raise ArchiveBlocked("Unknown backup source")
    if type(proof_version) is not int or proof_version not in {2, 3, 4}:
        raise ArchiveBlocked("Unsupported master-data proof version")
    if proof_version == 4:
        from .backup_scope import names

        return names(source, schema_scope, full_tables=MASTER_TABLES)
    if source == "direct":
        return DIRECT_MASTER_TABLES
    return LEGACY_MASTER_TABLES_V2 if proof_version == 2 else MASTER_TABLES


def check_master_tables(
    db, source, *, checkpoint, proof_version=MASTER_PROOF_VERSION, schema_scope=None
):
    """Fail before dumping if the explicit recovery scope is incomplete."""
    from sqlalchemy import inspect, text

    names = master_tables(source, proof_version=proof_version, schema_scope=schema_scope)
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


def require_current(receipts, *, source, environment, destination, now, schema_scope=None):
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
        version = files[0].get("master_proof_version") if files else None
        from . import schema_provider

        provider_format = bool(files and files[0].get("backup_format") == schema_provider.FORMAT)
        if provider_format:
            if not schema_provider.current_descriptor(files, source):
                continue
        elif (
            not files
            or type(version) is not int
            or (
                source == "gateway"
                and not (
                    (version == 4 and current_v4_descriptor(files))
                    or (
                        version == MASTER_PROOF_VERSION
                        and files[0].get("backup_format") in (None, "schema-master-v2")
                    )
                )
            )
            or (
                source == "direct"
                and (
                    version != MASTER_PROOF_VERSION
                    or files[0].get("backup_format") not in (None, "schema-master-v2")
                )
            )
        ):
            continue
        if (version == 4 and not provider_format) or (provider_format and source == "gateway"):
            live = schema_scope() if callable(schema_scope) else schema_scope
            if live is None or live != files[0]["master_schema_scope"]:
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
            and utc(snapshot.get("checked_at", "1970-01-01T00:00:00+00:00")) == checked
            and now - checked <= timedelta(hours=24)
        ):
            return receipt
    raise ArchiveBlocked("Current snapshot-verified schema and master-data backup required")


def validate_master_proof(proof, source):
    import re

    if (
        not isinstance(proof, dict)
        or type(proof.get("version")) is not int
        or proof["version"] not in (2, 3, 4, 5)
    ):
        raise ArchiveBlocked("Backup lacks typed master-data proof; retain legacy dumps")
    version = proof["version"]
    if (source == "gateway" and version == 5) or (source == "direct" and version == 4):
        from .schema_provider import validate_proof

        return validate_proof(proof, source)
    if version == 4 and set(proof) != {"version", "tables", "schema_scope"}:
        raise ArchiveBlocked("Explicit V4 master-data proof required")
    names = master_tables(source, proof_version=version, schema_scope=proof.get("schema_scope"))
    tables = proof.get("tables")
    if (
        not isinstance(tables, dict)
        or set(tables) != set(names)
        or any(
            not isinstance(v, dict)
            or (version == 4 and set(v) != {"rows", "content_sha256"})
            or type(v.get("rows")) is not int
            or v["rows"] < 0
            or not re.fullmatch("[a-f0-9]{64}", str(v.get("content_sha256", "")))
            for v in tables.values()
        )
    ):
        raise ArchiveBlocked("Incomplete typed master-data proof")
    return names


def v4_descriptor(proof, files):
    from .backup_scope import FORMAT
    from .codec import digest

    validate_master_proof(proof, "gateway")
    if proof["version"] != 4 or len(files) != 3:
        raise ArchiveBlocked("Complete V4 authenticated file set required")
    return dict(
        backup_format=FORMAT,
        master_proof_version=4,
        master_schema_scope=proof["schema_scope"],
        master_proof_sha256=digest(proof),
        master_table_names=sorted(proof["tables"]),
        master_auth_plaintext_sha256=files[2]["plaintext_sha256"],
        master_dump_plaintext_sha256=[v["plaintext_sha256"] for v in files[:2]],
    )


def current_v4_descriptor(files):
    import re

    from .backup_scope import FORMAT, validate_scope

    try:
        if (
            len(files) != 4
            or files[0].get("backup_format") != FORMAT
            or [f.get("format") for f in files] != ["parquet", "bin", "bin", "bin"]
            or type(files[3].get("bytes")) is not int
            or not 0 < files[3]["bytes"] <= AUTH_MAX_BYTES + 128
        ):
            return False
        primary = files[0]
        scope = validate_scope(primary["master_schema_scope"])
        names = master_tables("gateway", proof_version=4, schema_scope=scope)
        return (
            primary["master_table_names"] == sorted(names)
            and isinstance(primary["master_proof_sha256"], str)
            and bool(re.fullmatch("[a-f0-9]{64}", primary["master_proof_sha256"]))
            and primary["master_auth_plaintext_sha256"] == files[3]["plaintext_sha256"]
            and primary["master_dump_plaintext_sha256"]
            == [f["plaintext_sha256"] for f in files[1:3]]
            and all(
                isinstance(f.get("plaintext_sha256"), str)
                and re.fullmatch("[a-f0-9]{64}", f["plaintext_sha256"])
                for f in files[1:]
            )
        )
    except (ArchiveBlocked, KeyError, TypeError, ValueError):
        return False


def seal_master_proof(folder, proof, source, at, files, key, *, checkpoint):
    from .backup_scope import AUTH_FORMAT
    from .codec import digest, encode

    validate_master_proof(proof, source)
    if proof["version"] != 4 or source != "gateway" or len(files) != 2:
        raise ArchiveBlocked("Explicit V4 Gateway proof authentication required")
    value = dict(
        schema=AUTH_FORMAT,
        version=4,
        source=source,
        captured_at=at,
        master_proof=proof,
        master_proof_sha256=digest(proof),
        dump_files=files,
    )
    raw = encode(value)
    if len(raw) > AUTH_MAX_BYTES:
        raise ArchiveBlocked("Authenticated master metadata exceeds its bounded budget")
    plain = Path(folder) / "proof-auth.json"
    sealed = Path(folder) / "proof-auth.encrypted"
    with plain.open("xb") as stream:
        os.chmod(plain, 0o600)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        metadata = encrypt(plain, sealed, key, checkpoint=checkpoint)
        returned = Path(folder) / "proof-auth.verify"
        try:
            decrypt(sealed, returned, key, metadata["plaintext_sha256"], checkpoint=checkpoint)
        finally:
            returned.unlink(missing_ok=True)
        return sealed, metadata
    finally:
        plain.unlink(missing_ok=True)


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


def master_proof(db, source, *, checkpoint, proof_version=MASTER_PROOF_VERSION, schema_scope=None):
    """Bounded typed readback in the caller's consistent database transaction."""
    from sqlalchemy import MetaData, Table, select, text

    from .codec import encode

    if proof_version == 4:
        from .backup_scope import capture_scope

        actual = capture_scope(db, checkpoint=checkpoint)
        if schema_scope is not None and actual != schema_scope:
            raise ArchiveBlocked("Master schema scope changed during capture")
        schema_scope = actual
    tables = check_master_tables(
        db, source, checkpoint=checkpoint, proof_version=proof_version, schema_scope=schema_scope
    )
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
    proof = {"version": proof_version, "tables": result}
    if proof_version == 4:
        if capture_scope(db, checkpoint=checkpoint) != schema_scope:
            raise ArchiveBlocked("Master schema scope changed during capture")
        proof["schema_scope"] = schema_scope
    return proof


def fresh_schema_scope(database_url, *, checkpoint):
    """Independent short RO catalog snapshot; never shares the dump's old snapshot."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool

    from .backup_scope import capture_scope

    engine = create_engine(database_url, poolclass=NullPool, connect_args={"connect_timeout": 3})
    try:
        with (
            engine.connect().execution_options(isolation_level="REPEATABLE READ") as db,
            db.begin(),
        ):
            db.execute(text("SET TRANSACTION READ ONLY"))
            db.execute(text("SET LOCAL statement_timeout='8000ms'"))
            db.execute(text("SET LOCAL lock_timeout='150ms'"))
            return capture_scope(db, checkpoint=checkpoint)
    finally:
        engine.dispose()


def dump(
    database_url,
    source,
    output,
    *,
    checkpoint,
    executable=None,
    with_proof=False,
    provider_scope=None,
    schema_capture=None,
    audit_capture=None,
    parent_capture=None,
):
    """One exported snapshot spans schema and allowlisted master rows."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    if parent_capture is not None and audit_capture is not None:
        raise ArchiveBlocked("Combined parent and SQL-audit Source relay is not yet available")
    # V2/V3 remain unchanged file-restorable contracts. New Gateway captures
    # use an explicit V4 scope; Direct has no Alembic/auth-table contract.
    if provider_scope is not None:
        from . import schema_provider
        from .native_backup import source_url

        if os.geteuid() != 0 or not with_proof or executable is not None:
            raise ArchiveBlocked(
                "Root typed provider capture with pinned PostgreSQL clients required"
            )
        schema_provider.validate_scope(provider_scope)
        if provider_scope["source"] != source:
            raise ArchiveBlocked("Exact provider source required")
        source_url(
            database_url, source, provider_scope["environment"], provider_scope=provider_scope
        )
    if schema_capture is not None:
        from .source_schema import SourceSchemaCapture

        if (
            type(schema_capture) is not SourceSchemaCapture
            or provider_scope is None
            or os.geteuid() != 0
            or not with_proof
            or executable is not None
        ):
            raise ArchiveBlocked("Exact Root provider schema capture hook required")
        schema_capture.require_scope(provider_scope)
    if audit_capture is not None:
        from .sql_audit_v3_backup import AuditSnapshotCapture

        if (
            type(audit_capture) is not AuditSnapshotCapture
            or source != "gateway"
            or provider_scope is None
            or not with_proof
            or audit_capture.output != Path(output)
        ):
            raise ArchiveBlocked("Exact provider-bound V3 audit capture required")
    if parent_capture is not None:
        from .parent_snapshot import ParentSnapshotCapture

        if (type(parent_capture) is not ParentSnapshotCapture or provider_scope is None
            or not with_proof or parent_capture.source != source
            or parent_capture.output != Path(output) or os.geteuid() != 0):
            raise ArchiveBlocked("Exact Root same-snapshot parent hook required")
        parent_capture.require_scope(provider_scope)
    version = 4 if source == "gateway" else MASTER_PROOF_VERSION
    scope = fresh_schema_scope(database_url, checkpoint=checkpoint) if version == 4 else None
    tables = master_tables(source, proof_version=version, schema_scope=scope)
    url = make_url(database_url)
    env = {
        **({} if provider_scope is not None else os.environ),
        **({"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"} if provider_scope is not None else {}),
        "PGHOST": url.host or "localhost",
        "PGPORT": str(url.port or 5432),
        "PGDATABASE": url.database,
        "PGUSER": url.username,
        "PGPASSWORD": url.password or "",
        "PGCONNECT_TIMEOUT": "3",
        "PGOPTIONS": "-c statement_timeout=8000 -c lock_timeout=150"
        + (" -c default_transaction_read_only=on" if provider_scope is not None else ""),
    }
    engine = create_engine(database_url, pool_size=1, max_overflow=0)
    try:
        with (
            engine.connect().execution_options(isolation_level="REPEATABLE READ") as db,
            db.begin(),
        ):
            db.execute(text("SET TRANSACTION READ ONLY"))
            check_master_tables(
                db, source, checkpoint=checkpoint, proof_version=version, schema_scope=scope
            )
            if version == 4:
                from .backup_scope import capture_scope

                if capture_scope(db, checkpoint=checkpoint) != scope:
                    raise ArchiveBlocked("Master schema scope changed before dump")
            if provider_scope is not None:
                from . import native_backup
                from .policy import utc

                schema_provider.capture_provider(db, provider_scope, checkpoint=checkpoint)
                captured_at = utc(db.execute(text("SELECT transaction_timestamp()")).scalar_one())
                provider_state = {
                    "scope": provider_scope,
                    "structure": native_backup.structure_proof(db, checkpoint=checkpoint),
                    "jobs_sha256": native_backup.jobs_proof(db),
                    "sequences": native_backup.sequence_proof(db, checkpoint=checkpoint),
                }
            snapshot = db.execute(text("SELECT pg_export_snapshot()")).scalar_one()
            if parent_capture is not None:
                parent_capture.capture(db, snapshot, checkpoint=checkpoint)
            if audit_capture is not None:
                audit_capture.capture(db, snapshot, checkpoint=checkpoint)
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
                *(
                    [
                        (
                            "provider",
                            [
                                "--data-only",
                                "--no-blobs",
                                "--strict-names",
                                *[
                                    flag
                                    for relation in provider_scope["config_relations"]
                                    for flag in (
                                        "--table",
                                        '"' + relation["schema"] + '"."' + relation["name"] + '"',
                                    )
                                ],
                            ],
                        )
                    ]
                    if provider_scope is not None
                    else []
                ),
            ]:
                target = output / (label + ".dump")
                if label == "schema" and schema_capture is not None:
                    schema_capture.capture(
                        db=db,
                        source=source,
                        snapshot=snapshot,
                        target=target,
                        checkpoint=checkpoint,
                    )
                    outputs.append(target)
                    continue
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
            proof = (
                master_proof(
                    db, source, checkpoint=checkpoint, proof_version=version, schema_scope=scope
                )
                if with_proof
                else None
            )
            if version == 4 and fresh_schema_scope(database_url, checkpoint=checkpoint) != scope:
                raise ArchiveBlocked("Master schema scope changed between captures")
            if provider_scope is not None:
                for role, path in zip(("schema", "masters", "provider"), outputs, strict=True):
                    schema_provider.inspect_dump(
                        path, provider_scope, role, tables, env, checkpoint=checkpoint
                    )
                schema_provider.capture_provider(db, provider_scope, checkpoint=checkpoint)
                if (
                    native_backup.sequence_proof(db, checkpoint=checkpoint)
                    != provider_state["sequences"]
                ):
                    raise ArchiveBlocked(
                        "Source sequence changed during consistent provider capture"
                    )
                proof = {
                    **proof,
                    "version": schema_provider.proof_version(source),
                    "provider": provider_state,
                }
                schema_provider.validate_proof(proof, source)
                if audit_capture is not None:
                    if audit_capture.reference is None:
                        raise ArchiveBlocked("V3 audit capture unavailable")
                    return outputs, proof, captured_at, audit_capture.reference
                return outputs, proof, captured_at
            if with_proof:
                return outputs, proof
            return outputs
    finally:
        engine.dispose()
