"""Archive side of one encrypted Source-root RR audit snapshot exchange."""

import base64
import hashlib
import json
import os
import re
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.raw_archive.coordination import bound_job_lock
from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.remote_lease import require_bound_source_lease

from . import sql_audit_v3_backup, sql_audit_v3_source_snapshot
from .ledger import _private_read
from .restore_inputs import frozen


def need(value, reason):
    if not value:
        raise ArchiveBlocked(reason)


def decrypt_frame(private, request, frame):
    """Authenticate both ephemeral keys, complete request, and bounded payload."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    keys = {
        "format",
        "lease_session",
        "snapshot_sha256",
        "nonce",
        "source_public",
        "iv",
        "ciphertext",
        "plaintext_sha256",
    }
    need(
        type(frame) is dict
        and set(frame) == keys
        and frame["format"] == "greenmind.sql-v3-snapshot-cipher.v1"
        and frame["lease_session"] == request["lease_session"]
        and frame["nonce"] == request["nonce"]
        and frame["snapshot_sha256"] == hashlib.sha256(request["snapshot"].encode()).hexdigest()
        and all(
            type(frame[k]) is str and re.fullmatch(r"[a-f0-9]{64}", frame[k])
            for k in ("source_public", "plaintext_sha256")
        )
        and type(frame["iv"]) is str
        and re.fullmatch(r"[a-f0-9]{24}", frame["iv"])
        and type(frame["ciphertext"]) is str,
        "Exact encrypted Source snapshot frame required",
    )
    try:
        ciphertext = base64.b64decode(frame["ciphertext"], validate=True)
        need(16 <= len(ciphertext) <= 4 * 1024 * 1024 + 16, "Source snapshot cipher bound")
        source = X25519PublicKey.from_public_bytes(bytes.fromhex(frame["source_public"]))
        key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=bytes.fromhex(request["nonce"]),
            info=b"greenmind-sql-v3-source-snapshot-v1",
        ).derive(private.exchange(source))
        raw = AESGCM(key).decrypt(
            bytes.fromhex(frame["iv"]),
            ciphertext,
            sql_audit_v3_source_snapshot.aad(request, frame["source_public"]),
        )
    except Exception as exc:  # noqa: BLE001 - cryptographic failures have no safe detailed text
        raise ArchiveBlocked("Source snapshot ciphertext authentication failed") from exc
    need(
        hashlib.sha256(raw).hexdigest() == frame["plaintext_sha256"],
        "Source snapshot plaintext digest differs",
    )
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ArchiveBlocked("Source snapshot encoding differs") from exc
    if type(value) is dict and value.get("format") == "greenmind.sql-v3-audit-schema.v1":
        need(
            set(value) == {"format", "audit", "schema"} and type(value["schema"]) is str,
            "Source schema envelope differs",
        )
        try:
            schema = base64.b64decode(value["schema"], validate=True)
        except Exception as exc:  # noqa: BLE001
            raise ArchiveBlocked("Source schema encoding differs") from exc
        need(
            schema.startswith(b"PGDMP")
            and len(schema) <= sql_audit_v3_source_snapshot.SCHEMA_BYTES,
            "Source schema bound differs",
        )
        return sql_audit_v3_backup.validate(value["audit"]), schema
    return sql_audit_v3_backup.validate(value)


class RootSnapshotBridge:
    """One use only, live Archive→Source lease; actual relay is externally supervised."""

    def __init__(
        self,
        *,
        binding_path,
        binding_sha256,
        dba_identity_path,
        dba_identity_sha256,
        challenge_file,
        frame_file,
        source_manifest_sha256,
        producer_sha256,
        pg_client_dir=None,
        pg_client_manifest_sha256=None,
        crypto_runtime_dir=None,
        crypto_manifest_sha256=None,
    ):
        self.binding_path, self.binding_sha256 = Path(binding_path), binding_sha256
        self.dba_identity_path, self.dba_identity_sha256 = (
            Path(dba_identity_path),
            dba_identity_sha256,
        )
        self.challenge_file, self.frame_file = Path(challenge_file), Path(frame_file)
        self.source_manifest_sha256, self.producer_sha256 = source_manifest_sha256, producer_sha256
        self.pg_client_dir, self.pg_client_manifest_sha256 = (
            pg_client_dir,
            pg_client_manifest_sha256,
        )
        self.crypto_runtime_dir, self.crypto_manifest_sha256 = (
            crypto_runtime_dir,
            crypto_manifest_sha256,
        )
        self.used = False
        self.schema_scope = self.schema = self.snapshot = self.response_dir = None

    def schema_capture(self, scope, output):
        from .source_schema import SourceSchemaCapture

        self.schema_scope = scope
        self.response_parent = Path(output)
        return SourceSchemaCapture(
            scope,
            private_response=self.schema_response,
            source_program_sha256=self.producer_sha256,
            transport_binding_sha256=self.binding_sha256,
        )

    def schema_response(self, request, *, checkpoint):
        import tempfile

        from .codec import digest

        need(
            self.schema is not None
            and request.get("snapshot") == self.snapshot
            and request.get("scope_sha256") == digest(self.schema_scope)
            and request.get("program_sha256") == self.producer_sha256
            and request.get("transport_binding_sha256") == self.binding_sha256,
            "Authenticated Source schema request differs",
        )
        checkpoint()
        folder = Path(tempfile.mkdtemp(prefix="source-schema-", dir=self.response_parent))
        self.response_dir = folder
        schema = self.schema
        self.schema = None
        path = folder / "schema.dump"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(schema)
            stream.flush()
            os.fsync(stream.fileno())
        response = {
            "format": "greenmind.private-source-schema-response.v1",
            "request_sha256": digest(request),
            "owned_wait_exit": 0,
            "source_unchanged": True,
            "schema": {
                "name": "schema.dump",
                "bytes": len(schema),
                "sha256": hashlib.sha256(schema).hexdigest(),
            },
        }
        raw = json.dumps(response, sort_keys=True, separators=(",", ":")).encode()
        fd = os.open(
            folder / "response.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        checkpoint()
        return folder, hashlib.sha256(raw).hexdigest()

    def cleanup(self):
        self.schema = None
        if self.response_dir is not None:
            for name in ("schema.dump", "response.json"):
                (self.response_dir / name).unlink(missing_ok=True)
            self.response_dir.rmdir()
            self.response_dir = None

    def capture(
        self,
        snapshot,
        *,
        reader_db,
        reader_identity,
        schema_sha256,
        epoch,
        genesis_sha256,
        checkpoint,
    ):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

        from app.raw_archive import remote_lease
        from app.raw_archive.scientific_scope_control import _private_json, current_source_session

        from . import source_schema
        from .codec import digest
        from .sql_deletion_guard import _identity
        from .sql_runtime import _await_source_frame

        need(not self.used, "Source snapshot bridge cannot be replayed")
        self.used = True
        need(
            self.schema_scope is not None
            and type(self.pg_client_dir) is str
            and type(self.pg_client_manifest_sha256) is str
            and re.fullmatch(r"[a-f0-9]{64}", self.pg_client_manifest_sha256),
            "Pinned Source schema capture and PG client required",
        )
        need(
            os.geteuid() == 0
            and os.getenv("RAW_ARCHIVE_ENVIRONMENT") == "production"
            and os.getenv("RAW_ARCHIVE_WORKER_ROLE") == "archive"
            and os.getenv("TIERED_ARCHIVE_SQL_V3_SOURCE_SNAPSHOT_ENABLED") == "true"
            and type(reader_db) is Connection
            and reader_db.in_transaction()
            and callable(checkpoint),
            "Explicit root Source snapshot bridge required",
        )
        bound_job_lock()
        require_bound_source_lease()
        need(
            type(snapshot) is str
            and re.fullmatch(r"[A-Fa-f0-9-]{3,128}", snapshot)
            and type(reader_identity) is dict
            and _identity(reader_db) == reader_identity
            and reader_db.execute(text("SHOW transaction_isolation")).scalar_one()
            == "repeatable read"
            and reader_db.execute(text("SHOW transaction_read_only")).scalar_one() == "on",
            "Exporting reader RR snapshot differs",
        )
        binding = _private_json(self.binding_path, self.binding_sha256, limit=16384)
        dba_identity = _private_json(self.dba_identity_path, self.dba_identity_sha256, limit=16384)
        root = Path(__file__).resolve().parents[3]
        manifest = _private_json(root / "manifest.json", self.source_manifest_sha256)
        source_path = Path(sql_audit_v3_source_snapshot.__file__).resolve()
        original = frozen(source_path)
        source_raw = _private_read(source_path, 1024 * 1024)
        need(
            frozen(source_path) == original
            and hashlib.sha256(source_raw).hexdigest() == self.producer_sha256
            and manifest.get("files", {}).get(
                "backend/app/tiered_archive/sql_audit_v3_source_snapshot.py"
            )
            == self.producer_sha256,
            "Pinned Source snapshot producer differs",
        )
        from app.raw_archive.snapshot_api import check_loaded_function_code

        for module, names in (
            (
                sys.modules[__name__],
                (
                    "RootSnapshotBridge.capture",
                    "RootSnapshotBridge.schema_capture",
                    "RootSnapshotBridge.schema_response",
                    "RootSnapshotBridge.cleanup",
                    "decrypt_frame",
                    "request_snapshot_sha",
                ),
            ),
            (sql_audit_v3_source_snapshot, ("aad", "canonical")),
            (
                sql_audit_v3_backup,
                ("AuditSnapshotCapture.capture", "snapshot_payload", "validate", "decode"),
            ),
            (source_schema, ("SourceSchemaCapture.capture", "SourceSchemaCapture.require_scope")),
        ):
            path = Path(module.__file__).resolve()
            before = frozen(path)
            raw = _private_read(path, 1024 * 1024)
            need(
                frozen(path) == before
                and hashlib.sha256(raw).hexdigest()
                == manifest.get("files", {}).get(path.relative_to(root).as_posix()),
                "Archive snapshot dependency changed",
            )
            check_loaded_function_code(module, path, raw, names)
        session = current_source_session()
        authority = remote_lease._BOUND_SOURCE_LEASE.get()
        remote = getattr(authority[0], "__self__", None) if authority else None
        need(
            type(remote) is remote_lease.RemoteLease and remote.session == session,
            "Live Source lease required",
        )
        seconds = min(35, int(remote.deadline - time.monotonic()) - 10)
        need(seconds >= 3, "Source snapshot lease expires too soon")
        private = X25519PrivateKey.generate()
        public = (
            private.public_key()
            .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            .hex()
        )
        request = {
            "format": "greenmind.sql-v3-source-snapshot-request.v1",
            "snapshot": snapshot,
            "lease_session": session,
            "nonce": os.urandom(32).hex(),
            "archive_public": public,
            "binding_sha256": hashlib.sha256(
                sql_audit_v3_source_snapshot.canonical(binding)
            ).hexdigest(),
            "dba_identity": dba_identity,
            "reader_identity": reader_identity,
            "schema_sha256": schema_sha256,
            "epoch": epoch,
            "genesis_sha256": genesis_sha256,
            "expires_at": (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat(),
            "producer_sha256": self.producer_sha256,
            "source_manifest_sha256": self.source_manifest_sha256,
            "schema_scope_sha256": digest(self.schema_scope),
            "pg_client_dir": self.pg_client_dir,
            "pg_client_manifest_sha256": self.pg_client_manifest_sha256,
            "crypto_runtime_dir": self.crypto_runtime_dir,
            "crypto_manifest_sha256": self.crypto_manifest_sha256,
        }
        frame_sha = _await_source_frame(
            self.challenge_file,
            self.frame_file,
            "sql-v3-snapshot",
            {"request": request, "binding": binding},
            checkpoint,
            seconds=seconds,
            maximum=6 * 1024 * 1024,
        )
        first = frozen(self.frame_file)
        frame_raw = _private_read(self.frame_file, 6 * 1024 * 1024)
        need(
            frozen(self.frame_file) == first and hashlib.sha256(frame_raw).hexdigest() == frame_sha,
            "Encrypted Source frame changed",
        )
        try:
            frame = json.loads(frame_raw)
        except (ValueError, UnicodeError) as exc:
            raise ArchiveBlocked("Invalid encrypted Source frame") from exc
        combined = decrypt_frame(private, request, frame)
        need(type(combined) is tuple and len(combined) == 2, "Authenticated Source schema missing")
        payload, self.schema = combined
        self.snapshot = snapshot
        need(
            payload["source_identity"] == dba_identity
            and payload["schema_sha256"] == schema_sha256
            and payload["snapshot_sha256"] == request_snapshot_sha(snapshot)
            and payload["data"]["generation"][0]["epoch"] == epoch
            and payload["data"]["generation"][0]["genesis_sha"] == genesis_sha256,
            "Imported Source snapshot payload differs",
        )
        checkpoint()
        need(
            current_source_session() == session
            and reader_db.in_transaction()
            and _identity(reader_db) == reader_identity,
            "Exporting Source snapshot or lease changed",
        )
        return payload


def request_snapshot_sha(snapshot):
    return hashlib.sha256(snapshot.encode()).hexdigest()
