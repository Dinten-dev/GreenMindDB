"""Root-owned same-exported-snapshot parent hook for provider backup.

Only a completed local backup can be joined; this never declares publication,
restore, old Science compatibility, cross-database atomicity, or deletion safe.
"""

import base64
import hashlib
import json
import os
import re
import stat
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.raw_archive.coordination import bound_job_lock
from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.remote_lease import require_bound_source_lease

from .codec import digest


def need(value, message):
    if not value:
        raise ArchiveBlocked(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def snapshot_sha(snapshot):
    return hashlib.sha256(snapshot.encode()).hexdigest()


def completed_file(path, *, checkpoint):
    """Hash the exact Root-owned inode, rechecking generation while bounded."""
    path = Path(path)
    before = path.lstat()
    need(
        stat.S_ISREG(before.st_mode)
        and before.st_uid == 0
        and before.st_nlink == 1
        and stat.S_IMODE(before.st_mode) == 0o600
        and 0 < before.st_size <= 64 * 1024**2,
        "Root-private completed backup file required",
    )
    fields = (
        "st_dev",
        "st_ino",
        "st_mode",
        "st_uid",
        "st_nlink",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )

    def identity(item):
        return tuple(getattr(item, key) for key in fields)

    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    digest = hashlib.sha256()
    with os.fdopen(fd, "rb") as stream:
        need(
            identity(os.fstat(stream.fileno())) == identity(before),
            "Completed backup inode changed",
        )
        while block := stream.read(1024 * 1024):
            checkpoint()
            digest.update(block)
        need(
            identity(os.fstat(stream.fileno())) == identity(before)
            and identity(path.lstat()) == identity(before),
            "Completed backup inode changed",
        )
    return before.st_size, digest.hexdigest()


def aad(request, source_public):
    return canonical(
        {
            "format": "greenmind.parent-snapshot-cipher.v1",
            "request_sha256": hashlib.sha256(canonical(request)).hexdigest(),
            "source_public": source_public,
        }
    )


def decrypt_frame(private, request, frame):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    fields = {
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
        and set(frame) == fields
        and frame["format"] == "greenmind.parent-snapshot-cipher.v1"
        and frame["lease_session"] == request["lease_session"]
        and frame["nonce"] == request["nonce"]
        and frame["snapshot_sha256"] == snapshot_sha(request["snapshot"])
        and all(
            type(frame[k]) is str and re.fullmatch(r"[a-f0-9]{64}", frame[k])
            for k in ("source_public", "plaintext_sha256")
        )
        and type(frame["iv"]) is str
        and re.fullmatch(r"[a-f0-9]{24}", frame["iv"])
        and type(frame["ciphertext"]) is str,
        "Parent encrypted frame differs",
    )
    try:
        ciphertext = base64.b64decode(frame["ciphertext"], validate=True)
        need(16 <= len(ciphertext) <= 512 * 1024, "Parent frame exceeds bound")
        source = X25519PublicKey.from_public_bytes(bytes.fromhex(frame["source_public"]))
        key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=bytes.fromhex(request["nonce"]),
            info=b"greenmind-parent-snapshot-v1",
        ).derive(private.exchange(source))
        raw = AESGCM(key).decrypt(
            bytes.fromhex(frame["iv"]), ciphertext, aad(request, frame["source_public"])
        )
        value = json.loads(raw)
    except Exception as exc:  # noqa: BLE001 - ciphertext errors contain no safe detail
        raise ArchiveBlocked("Parent encrypted frame authentication failed") from exc
    need(
        hashlib.sha256(raw).hexdigest() == frame["plaintext_sha256"] and canonical(value) == raw,
        "Parent frame plaintext changed",
    )
    return value


def direct_closure(db, *, checkpoint):
    checkpoint()
    row = db.execute(
        text("""SELECT count(*)::bigint,
        count(*) FILTER (WHERE d.id IS NULL)::bigint,
        count(*) FILTER (WHERE s.id IS NULL)::bigint
        FROM public.direct_segment x LEFT JOIN public.direct_device d ON d.id=x.device_id
        LEFT JOIN public.direct_session s ON s.device_id=x.device_id AND s.id=x.session_id""")
    ).one()
    need(
        len(row) == 3 and all(type(n) is int and n >= 0 for n in row) and row[1] == row[2] == 0,
        "Direct parent/session closure incomplete",
    )
    checkpoint()
    return {"segments": row[0], "missing_devices": row[1], "missing_sessions": row[2]}


class ParentSnapshotCapture:
    """One live snapshot exchange; all request and completion proof fields are bound."""

    def __init__(
        self,
        *,
        source,
        output,
        provider_scope_sha256,
        reference_master_sha256,
        source_binding_path,
        source_binding_sha256,
        source_manifest_sha256,
        producer_sha256,
        challenge_file,
        frame_file,
        native_library,
        native_sha256,
        crypto_runtime_dir,
        crypto_manifest_sha256,
        receipt_dir,
    ):
        from app.raw_archive.parent_capture import DATABASES

        need(
            source in DATABASES
            and type(provider_scope_sha256) is str
            and re.fullmatch(r"[a-f0-9]{64}", provider_scope_sha256)
            and all(
                type(x) is str and re.fullmatch(r"[a-f0-9]{64}", x)
                for x in (
                    reference_master_sha256,
                    source_binding_sha256,
                    source_manifest_sha256,
                    producer_sha256,
                    native_sha256,
                    crypto_manifest_sha256,
                )
            ),
            "Pinned same-source parent scope required",
        )
        output = Path(output)
        need(
            output.is_absolute()
            and output.resolve() == output
            and output.is_dir()
            and output.stat().st_uid == 0
            and stat.S_IMODE(output.stat().st_mode) == 0o700,
            "Root-private backup directory required",
        )
        self.source, self.output, self.provider_scope_sha256 = source, output, provider_scope_sha256
        self.reference_master_sha256 = reference_master_sha256
        self.source_binding_path, self.source_binding_sha256 = (
            Path(source_binding_path),
            source_binding_sha256,
        )
        self.source_manifest_sha256, self.producer_sha256 = source_manifest_sha256, producer_sha256
        self.challenge_file, self.frame_file = Path(challenge_file), Path(frame_file)
        self.native_library, self.native_sha256 = str(native_library), native_sha256
        self.crypto_runtime_dir, self.crypto_manifest_sha256 = (
            str(crypto_runtime_dir),
            crypto_manifest_sha256,
        )
        self.receipt_dir = Path(receipt_dir)
        need(
            Path(self.native_library).is_absolute() and Path(self.crypto_runtime_dir).is_absolute(),
            "Pinned Source native validator and isolated crypto runtime required",
        )
        need(
            self.receipt_dir.is_absolute()
            and self.receipt_dir.resolve() == self.receipt_dir
            and self.receipt_dir.is_dir()
            and self.receipt_dir.stat().st_uid == 0
            and stat.S_IMODE(self.receipt_dir.stat().st_mode) == 0o700,
            "Existing Root-private parent receipt directory required",
        )
        self.used, self.result, self.scope = False, None, None

    def require_scope(self, scope):
        from .schema_provider import validate_scope

        validate_scope(scope)
        need(
            scope["source"] == self.source
            and scope["environment"] == "production"
            and digest(scope) == self.provider_scope_sha256,
            "Parent provider scope differs",
        )
        self.scope = scope

    def capture(self, db, snapshot, *, checkpoint):
        import sys

        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

        from app.raw_archive import remote_lease
        from app.raw_archive.parent_scope import SCOPE_SHA256
        from app.raw_archive.scientific_scope_control import _private_json, current_source_session

        from .ledger import _private_read
        from .restore_inputs import frozen
        from .sql_deletion_guard import _identity
        from .sql_runtime import _await_source_frame

        need(not self.used, "Parent snapshot capture cannot replay")
        self.used = True
        need(
            os.geteuid() == 0
            and os.getenv("RAW_ARCHIVE_ENVIRONMENT") == "production"
            and os.getenv("RAW_ARCHIVE_WORKER_ROLE") == "archive"
            and os.getenv("TIERED_ARCHIVE_PARENT_SNAPSHOT_ENABLED") == "true"
            and self.scope is not None
            and type(db) is Connection
            and db.in_transaction()
            and type(snapshot) is str
            and re.fullmatch(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{8}-[1-9][0-9]*", snapshot),
            "Live Root parent snapshot authority required",
        )
        bound_job_lock()
        require_bound_source_lease()
        identity = _identity(db)
        need(
            identity["database"] == self.scope["database"]
            and identity["database_oid"] == self.scope["source_binding"]["database_oid"]
            and identity["isolation"] == "repeatable read"
            and identity["read_only"] == "on",
            "Parent exporter physical database differs",
        )
        exporter_pid = db.execute(text("SELECT pg_backend_pid()")).scalar_one()
        semantic = db.execute(text("SELECT pg_current_snapshot()::text")).scalar_one()
        need(
            type(exporter_pid) is int and exporter_pid > 0 and type(semantic) is str and semantic,
            "Live PostgreSQL exporter required",
        )
        closure = direct_closure(db, checkpoint=checkpoint) if self.source == "direct" else None
        binding = _private_json(self.source_binding_path, self.source_binding_sha256, limit=16384)
        root = Path(__file__).resolve().parents[3]
        manifest = _private_json(root / "manifest.json", self.source_manifest_sha256)
        source_path = root / "backend/app/tiered_archive/parent_snapshot_source.py"
        source_first = frozen(source_path)
        source_raw = _private_read(source_path, 1024 * 1024)
        need(
            frozen(source_path) == source_first
            and hashlib.sha256(source_raw).hexdigest() == self.producer_sha256
            and manifest.get("files", {}).get(source_path.relative_to(root).as_posix())
            == self.producer_sha256,
            "Pinned Source parent producer differs",
        )
        from app.raw_archive import parent_capture, scientific_scope_control
        from app.raw_archive.snapshot_api import check_loaded_function_code

        from . import backup, schema_provider, sql_deletion_guard, sql_runtime

        for module, names in (
            (
                sys.modules[__name__],
                (
                    "ParentSnapshotCapture.capture",
                    "ParentSnapshotCapture.complete",
                    "ParentSnapshotCapture.record_publication",
                    "decrypt_frame",
                    "direct_closure",
                    "canonical",
                    "aad",
                    "completed_file",
                ),
            ),
            (backup, ("dump",)),
            (schema_provider, ("capture", "validate_scope", "validate_proof")),
            (parent_capture, ("validate_capture",)),
            (scientific_scope_control, ("_private_json", "current_source_session")),
            (sql_deletion_guard, ("_identity",)),
            (sql_runtime, ("_await_source_frame",)),
        ):
            path = Path(module.__file__).resolve()
            first = frozen(path)
            raw = _private_read(path, 1024 * 1024)
            need(
                frozen(path) == first
                and hashlib.sha256(raw).hexdigest()
                == manifest.get("files", {}).get(path.relative_to(root).as_posix()),
                "Parent backup runtime dependency differs",
            )
            check_loaded_function_code(module, path, raw, names)
        session = current_source_session()
        authority = remote_lease._BOUND_SOURCE_LEASE.get()
        remote = getattr(authority[0], "__self__", None) if authority else None
        need(
            type(remote) is remote_lease.RemoteLease and remote.session == session,
            "Live Source lease required",
        )
        seconds = min(45, int(remote.deadline - time.monotonic()) - 10)
        need(seconds >= 3, "Source lease expires too soon")
        private = X25519PrivateKey.generate()
        public = (
            private.public_key()
            .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            .hex()
        )
        request = {
            "format": "greenmind.parent-snapshot-request.v1",
            "source": self.source,
            "snapshot": snapshot,
            "snapshot_sha256": snapshot_sha(snapshot),
            "visible_snapshot_sha256": snapshot_sha(semantic),
            "exporter_pid": exporter_pid,
            "reader_identity": identity,
            "provider_scope_sha256": self.provider_scope_sha256,
            "parent_scope_sha256": SCOPE_SHA256,
            "reference_master_sha256": self.reference_master_sha256,
            "lease_session": session,
            "nonce": os.urandom(32).hex(),
            "capture_nonce": os.urandom(16).hex(),
            "archive_public": public,
            "binding_sha256": hashlib.sha256(canonical(binding)).hexdigest(),
            "source_manifest_sha256": self.source_manifest_sha256,
            "producer_sha256": self.producer_sha256,
            "native_library": self.native_library,
            "native_sha256": self.native_sha256,
            "crypto_runtime_dir": self.crypto_runtime_dir,
            "crypto_manifest_sha256": self.crypto_manifest_sha256,
            "expires_at": (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat(),
        }
        frame_sha = _await_source_frame(
            self.challenge_file,
            self.frame_file,
            "parent-snapshot",
            {"request": request, "binding": binding},
            checkpoint,
            seconds=seconds,
            maximum=1024 * 1024,
        )
        first = frozen(self.frame_file)
        raw = _private_read(self.frame_file, 1024 * 1024)
        need(
            frozen(self.frame_file) == first and hashlib.sha256(raw).hexdigest() == frame_sha,
            "Parent frame changed",
        )
        response = decrypt_frame(private, request, json.loads(raw))
        from app.raw_archive import parent_capture

        need(
            type(response) is dict
            and set(response)
            == {
                "format",
                "request_sha256",
                "capture_root",
                "capture_sha256",
                "capture",
                "source_lock",
                "source_container_generation",
                "native_index_pending",
            }
            and response["format"] == "greenmind.parent-snapshot-response.v1"
            and response["request_sha256"] == hashlib.sha256(canonical(request)).hexdigest()
            and re.fullmatch(r"[a-f0-9]{64}", response["capture_sha256"] or "")
            and response["capture_root"]
            == "/mnt/HC_Volume_106755700/greenmind-parent-snapshot-"
            + request["capture_nonce"]
            + "/capture"
            and response["native_index_pending"] is True
            and type(response["source_lock"]) is dict
            and set(response["source_lock"])
            == {
                "device",
                "inode",
                "pid",
                "uid",
                "start_ticks",
                "command_sha256",
                "executable_sha256",
            }
            and type(response["source_lock"]["pid"]) is int
            and response["source_lock"]["pid"] > 1
            and type(response["source_lock"]["start_ticks"]) is int
            and response["source_lock"]["start_ticks"] > 0
            and all(
                response["source_lock"].get(k) == binding.get(b)
                for k, b in (
                    ("device", "lock_device"),
                    ("inode", "lock_inode"),
                    ("uid", "lease_uid"),
                    ("command_sha256", "lease_command_sha256"),
                    ("executable_sha256", "lease_executable_sha256"),
                )
            )
            and type(response["source_container_generation"]) is dict
            and set(response["source_container_generation"]) == {"pid", "started_at"}
            and type(response["source_container_generation"]["pid"]) is int
            and response["source_container_generation"]["pid"] > 1
            and type(response["source_container_generation"]["started_at"]) is str
            and response["source_container_generation"]["started_at"],
            "Parent capture response differs",
        )
        proof = response["capture"]
        parent_capture.validate_capture(proof)
        source_identity = proof["source_identity"]
        need(
            hashlib.sha256(canonical(proof)).hexdigest() == response["capture_sha256"]
            and proof["kind"] == self.source
            and proof["scope_sha256"] == SCOPE_SHA256
            and proof["imported_snapshot_sha256"] == request["snapshot_sha256"]
            and proof["snapshot_sha256"] == request["visible_snapshot_sha256"]
            and proof["provider_scope_sha256"] == self.provider_scope_sha256
            and proof["schema_master_sha256"] == self.reference_master_sha256
            and all(
                source_identity[k] == identity[k]
                for k in (
                    "database",
                    "database_oid",
                    "role",
                    "session_role",
                    "role_oid",
                    "server_address",
                    "postmaster_start",
                )
            )
            and source_identity["port"] == identity["server_port"],
            "Imported parent snapshot proof differs",
        )
        if closure is not None:
            need(
                next(item["rows"] for item in proof["files"] if item["table"] == "direct_segment")
                == closure["segments"],
                "Direct captured rows differ from session closure",
            )
        checkpoint()
        need(
            current_source_session() == session
            and db.in_transaction()
            and _identity(db) == identity
            and db.execute(text("SELECT pg_backend_pid()")).scalar_one() == exporter_pid,
            "Parent exporter or lease changed",
        )
        self.result = {
            "request_sha256": hashlib.sha256(canonical(request)).hexdigest(),
            "snapshot_sha256": request["snapshot_sha256"],
            "visible_snapshot_sha256": request["visible_snapshot_sha256"],
            "capture_root": response["capture_root"],
            "capture_sha256": response["capture_sha256"],
            "capture_id": proof["capture_id"],
            "frame_sha256": frame_sha,
            "lease_session": session,
            "source_lock": response["source_lock"],
            "source_container_generation": response["source_container_generation"],
            "native_index_pending": True,
            "parent_scope_sha256": SCOPE_SHA256,
            "provider_scope_sha256": self.provider_scope_sha256,
            "reference_master_sha256": self.reference_master_sha256,
            "direct_closure": closure,
        }
        self._request, self._private = request, private
        self._frame_sha256 = frame_sha
        self._response_sha256 = hashlib.sha256(canonical(response)).hexdigest()
        return self.result

    def complete(self, candidate, files, proof, *, checkpoint):
        """Join only actual completed provider outputs; old five-file contract is unchanged."""
        from .ledger import _private_read
        from .policy import Candidate
        from .restore_inputs import frozen
        from .schema_provider import validate_proof

        need(
            self.used
            and self.result is not None
            and type(candidate) is Candidate
            and candidate.source == self.source
            and candidate.environment == "production"
            and candidate.dataset == "database_backup"
            and type(files) is list
            and len(files) in (5, 6),
            "Completed provider backup required",
        )
        candidate.validate()
        need(
            hasattr(self, "_private")
            and hasattr(self, "_request")
            and hasattr(self, "_frame_sha256")
            and hasattr(self, "_response_sha256"),
            "Authenticated parent Source frame required",
        )
        first = frozen(self.frame_file)
        raw_frame = _private_read(self.frame_file, 1024 * 1024)
        need(
            frozen(self.frame_file) == first
            and hashlib.sha256(raw_frame).hexdigest() == self._frame_sha256,
            "Parent Source frame changed before backup completion",
        )
        response = decrypt_frame(self._private, self._request, json.loads(raw_frame))
        need(
            hashlib.sha256(canonical(response)).hexdigest() == self._response_sha256
            and response.get("request_sha256") == self.result["request_sha256"]
            and response.get("capture_sha256") == self.result["capture_sha256"]
            and response.get("capture", {}).get("capture_id") == self.result["capture_id"],
            "Parent Source frame does not bind the completion join",
        )
        validate_proof(proof, self.source)
        need(
            digest(proof["provider"]["scope"]) == self.provider_scope_sha256,
            "Completed provider scope differs",
        )
        names = ("0000.parquet", "0001.bin", "0002.bin", "0003.bin", "0004.bin")
        refs = []
        for (path, reference), name in zip(files[:5], names, strict=True):
            checkpoint()
            path = Path(path)
            size, sha = completed_file(path, checkpoint=checkpoint)
            need(
                path.parent == self.output
                and path.name == name
                and type(reference) is dict
                and type(reference.get("bytes")) is int
                and reference["bytes"] == size
                and reference.get("sha256") == sha,
                "Completed provider file differs",
            )
            refs.append(reference)
        index = refs[0]
        need(
            index.get("content_sha256") == candidate.content_sha256
            and index.get("master_proof_sha256") == digest(proof)
            and index.get("provider_scope_sha256") == self.provider_scope_sha256,
            "Completed provider index differs",
        )
        source_dumps = []
        for label, reference in zip(("schema", "masters", "provider"), refs[1:4], strict=True):
            path = self.output / (label + ".dump")
            _, sha = completed_file(path, checkpoint=checkpoint)
            need(
                sha == reference.get("plaintext_sha256"),
                "Actual provider dump differs from sealed artifact",
            )
            source_dumps.append(
                {
                    "role": label,
                    "plaintext_sha256": reference["plaintext_sha256"],
                    "cipher_sha256": reference["sha256"],
                    "cipher_bytes": reference["bytes"],
                }
            )
        checkpoint()
        join = {
            "format": "greenmind.parent-provider-snapshot-join.v1",
            "source": self.source,
            "parent": self.result,
            "backup_candidate": candidate.identity,
            "backup_index_sha256": index["sha256"],
            "backup_index_content_sha256": index["content_sha256"],
            "backup_file_sha256": [item["sha256"] for item in refs],
            "master_proof_sha256": digest(proof),
            "provider_scope_sha256": self.provider_scope_sha256,
            "provider_dumps": source_dumps,
            "backup_local_complete": True,
            "parent_and_backup_same_exported_snapshot": True,
            "source_terminal_proof_required": True,
            "backup_published": False,
            "independent_backup_readback": False,
            "native_restore_verified": False,
            "archive_accepted": False,
            "cross_database_atomic": False,
            "same_snapshot_as_previous_science": False,
            "science_compatibility_proven": False,
        }
        raw = canonical(join)
        need(len(raw) <= 65536, "Parent completion proof exceeds bound")
        target = self.receipt_dir / ("parent-snapshot-join-" + self.result["capture_id"] + ".json")
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_WRONLY, 0o400)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        fd = os.open(self.receipt_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        self.join = {"path": str(target), "sha256": hashlib.sha256(raw).hexdigest(), "value": join}
        return self.join

    def record_publication(self, receipt, *, ledger, checkpoint):
        """Join the real returned manifest after publication; no readback claim."""
        from .ledger import Ledger, _private_read
        from .restore_inputs import frozen

        need(
            getattr(self, "join", None) is not None
            and type(receipt) is dict
            and type(ledger) is Ledger
            and not ledger.read_only
            and receipt.get("candidate") == self.join["value"]["backup_candidate"]
            and receipt.get("state") == "published"
            and type(receipt.get("files")) is list
            and [item.get("sha256") for item in receipt["files"]]
            == self.join["value"]["backup_file_sha256"]
            and type(receipt.get("manifest")) is dict
            and type(receipt["manifest"].get("sha256")) is str
            and re.fullmatch(r"[a-f0-9]{64}", receipt["manifest"]["sha256"])
            and ledger.get(receipt["candidate"]) == receipt,
            "Published provider receipt differs",
        )
        join_path = Path(self.join["path"])
        first = frozen(join_path)
        raw_join = _private_read(join_path, 65536)
        need(
            frozen(join_path) == first
            and hashlib.sha256(raw_join).hexdigest() == self.join["sha256"]
            and json.loads(raw_join) == self.join["value"],
            "Local parent completion join changed",
        )
        checkpoint()
        record = {
            "format": "greenmind.parent-provider-published-join.v1",
            "source": self.source,
            "local_join_sha256": self.join["sha256"],
            "backup_candidate": receipt["candidate"],
            "backup_manifest_sha256": receipt["manifest"]["sha256"],
            "backup_file_sha256": self.join["value"]["backup_file_sha256"],
            "backup_published": True,
            "independent_backup_readback": False,
            "source_terminal_proof_required": True,
            "archive_accepted": False,
        }
        raw = canonical(record)
        target = self.receipt_dir / (
            "parent-snapshot-published-" + self.result["capture_id"] + ".json"
        )
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_WRONLY, 0o400)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        fd = os.open(self.receipt_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return {"path": str(target), "sha256": hashlib.sha256(raw).hexdigest()}


def root_snapshot_capture(output, source, *, profile_path, profile_sha256):
    """Construct the opt-in bridge from one immutable Root-owned profile."""
    from app.raw_archive.scientific_scope_control import _private_json

    from .restore_inputs import protected_parents

    need(
        os.geteuid() == 0
        and os.getenv("RAW_ARCHIVE_ENVIRONMENT") == "production"
        and os.getenv("RAW_ARCHIVE_WORKER_ROLE") == "archive"
        and os.getenv("TIERED_ARCHIVE_PARENT_SNAPSHOT_ENABLED") == "true",
        "Explicit production Root parent snapshot required",
    )
    profile = _private_json(profile_path, profile_sha256, limit=16384)
    fields = {
        "format",
        "source",
        "provider_scope_sha256",
        "reference_master_sha256",
        "source_binding_path",
        "source_binding_sha256",
        "source_manifest_sha256",
        "producer_sha256",
        "challenge_file",
        "frame_file",
        "native_library",
        "native_sha256",
        "crypto_runtime_dir",
        "crypto_manifest_sha256",
        "receipt_dir",
    }
    need(
        type(profile) is dict
        and set(profile) == fields
        and profile["format"] == "greenmind.parent-snapshot-profile.v1"
        and profile["source"] == source,
        "Exact parent snapshot profile required",
    )
    for name in ("source_binding_path", "challenge_file", "frame_file", "receipt_dir"):
        need(
            type(profile[name]) is str and Path(profile[name]).is_absolute(),
            "Absolute Root parent path required",
        )
        protected_parents(Path(profile[name]))
    return ParentSnapshotCapture(
        source=source, output=output, **{k: profile[k] for k in fields - {"format", "source"}}
    )
