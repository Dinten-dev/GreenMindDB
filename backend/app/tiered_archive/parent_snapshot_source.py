"""Source-root imported RR parent capture; only an ephemeral cipher leaves Source."""

import base64
import hashlib
import os
import re
import socket
import stat
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from app.raw_archive.policy import ArchiveBlocked

from .parent_snapshot import aad, canonical, snapshot_sha


def need(value, message):
    if not value:
        raise ArchiveBlocked(message)


def _reader_profile_bytes(path, expected_sha256):
    """Read the existing 0600 profile without treating it as a 0400 restore input."""
    from .restore_inputs import protected_parents

    need(path.is_absolute() and path.resolve(strict=False) == path, "Source reader path differs")
    protected_parents(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        first = os.fstat(stream.fileno())
        need(
            stat.S_ISREG(first.st_mode)
            and first.st_uid == 0
            and first.st_nlink == 1
            and stat.S_IMODE(first.st_mode) == 0o600
            and 0 < first.st_size <= 32768,
            "Existing Source reader profile identity differs",
        )
        raw = stream.read(32769)
        after = os.fstat(stream.fileno())
    current = path.lstat()
    fields = (
        "st_dev",
        "st_ino",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
        "st_mode",
        "st_uid",
        "st_nlink",
    )
    need(
        len(raw) == first.st_size
        and all(getattr(first, k) == getattr(after, k) == getattr(current, k) for k in fields)
        and hashlib.sha256(raw).hexdigest() == expected_sha256,
        "Existing Source reader profile differs",
    )
    return raw


def encrypt_response(response, request):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    raw = canonical(response)
    need(len(raw) <= 256 * 1024, "Parent response bound")
    private = X25519PrivateKey.generate()
    public = (
        private.public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        .hex()
    )
    peer = X25519PublicKey.from_public_bytes(bytes.fromhex(request["archive_public"]))
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=bytes.fromhex(request["nonce"]),
        info=b"greenmind-parent-snapshot-v1",
    ).derive(private.exchange(peer))
    iv = os.urandom(12)
    cipher = AESGCM(key).encrypt(iv, raw, aad(request, public))
    return {
        "format": "greenmind.parent-snapshot-cipher.v1",
        "lease_session": request["lease_session"],
        "snapshot_sha256": request["snapshot_sha256"],
        "nonce": request["nonce"],
        "source_public": public,
        "iv": iv.hex(),
        "ciphertext": base64.b64encode(cipher).decode(),
        "plaintext_sha256": hashlib.sha256(raw).hexdigest(),
    }


def capture(request, binding, *, checkpoint):
    """Use actual Source lease and existing reader; export only encrypted metadata."""
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    from app.raw_archive import (
        parent_capture,
        remote_lease,
        scientific_capture,
        scientific_native,
        scientific_scope_control,
    )
    from app.raw_archive.parent_scope import SCOPE_SHA256
    from app.raw_archive.snapshot_api import check_loaded_function_code

    from .ledger import _private_read
    from .restore_inputs import frozen
    from .sql_audit_v3_crypto import MANIFEST_SHA256
    from .sql_audit_v3_source import _observe_container

    fields = {
        "format",
        "source",
        "snapshot",
        "snapshot_sha256",
        "visible_snapshot_sha256",
        "exporter_pid",
        "reader_identity",
        "provider_scope_sha256",
        "parent_scope_sha256",
        "reference_master_sha256",
        "lease_session",
        "nonce",
        "capture_nonce",
        "archive_public",
        "binding_sha256",
        "source_manifest_sha256",
        "producer_sha256",
        "native_library",
        "native_sha256",
        "crypto_runtime_dir",
        "crypto_manifest_sha256",
        "expires_at",
    }
    need(
        os.geteuid() == 0
        and os.getenv("TIERED_ARCHIVE_PARENT_SNAPSHOT_ENABLED") == "true"
        and callable(checkpoint)
        and type(request) is dict
        and set(request) == fields
        and request["format"] == "greenmind.parent-snapshot-request.v1"
        and request["source"] in {"gateway", "direct"}
        and type(request["snapshot"]) is str
        and re.fullmatch(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{8}-[1-9][0-9]*", request["snapshot"])
        and request["snapshot_sha256"] == snapshot_sha(request["snapshot"])
        and type(request["exporter_pid"]) is int
        and request["exporter_pid"] > 0
        and type(request["reader_identity"]) is dict
        and request["parent_scope_sha256"] == SCOPE_SHA256
        and type(request["lease_session"]) is str
        and re.fullmatch(r"[a-f0-9]{32}", request["lease_session"])
        and type(request["capture_nonce"]) is str
        and re.fullmatch(r"[a-f0-9]{32}", request["capture_nonce"])
        and all(
            type(request[k]) is str and re.fullmatch(r"[a-f0-9]{64}", request[k])
            for k in (
                "snapshot_sha256",
                "visible_snapshot_sha256",
                "provider_scope_sha256",
                "parent_scope_sha256",
                "reference_master_sha256",
                "nonce",
                "archive_public",
                "binding_sha256",
                "source_manifest_sha256",
                "producer_sha256",
                "native_sha256",
                "crypto_manifest_sha256",
            )
        )
        and request["crypto_manifest_sha256"] == MANIFEST_SHA256
        and type(request["crypto_runtime_dir"]) is str
        and Path(request["crypto_runtime_dir"]).is_absolute()
        and type(request["native_library"]) is str
        and Path(request["native_library"]).is_absolute()
        and type(binding) is dict
        and hashlib.sha256(canonical(binding)).hexdigest() == request["binding_sha256"],
        "Exact live Source parent request required",
    )
    identity = request["reader_identity"]
    expected = parent_capture.scope(request["source"])["identity"]
    need(
        identity.get("database") == expected["database"]
        and identity.get("database_oid") == expected["database_oid"]
        and identity.get("role")
        == identity.get("session_role")
        == "greenmind_archive_reader_33fe395"
        and identity.get("role_oid") == 499352
        and identity.get("read_only") == "on"
        and identity.get("isolation") == "repeatable read",
        "Parent exporter identity differs",
    )
    root = Path(__file__).resolve().parents[3]
    manifest = scientific_scope_control._private_json(
        root / "manifest.json", request["source_manifest_sha256"]
    )
    from . import parent_snapshot

    for module, names in (
        (sys.modules[__name__], ("capture", "encrypt_response", "_reader_profile_bytes")),
        (parent_snapshot, ("aad", "canonical", "snapshot_sha")),
        (parent_capture, ("capture", "scope", "_identity", "_schema")),
        (scientific_native, ("counter_factory", "_load_pinned_library")),
        (scientific_capture, ("_source_resources", "_cgroup_limits", "_engine_binding")),
        (scientific_scope_control, ("_source_lock",)),
        (remote_lease, ("source_health",)),
    ):
        path = Path(module.__file__).resolve()
        before = frozen(path)
        raw = _private_read(path, 1024 * 1024)
        expected_sha = manifest.get("files", {}).get(path.relative_to(root).as_posix())
        need(
            frozen(path) == before and hashlib.sha256(raw).hexdigest() == expected_sha,
            "Parent producer runtime manifest differs",
        )
        check_loaded_function_code(module, path, raw, names)
    need(
        manifest["files"][Path(__file__).resolve().relative_to(root).as_posix()]
        == request["producer_sha256"],
        "Parent producer SHA differs",
    )
    try:
        expires = datetime.fromisoformat(request["expires_at"])
        remaining = (expires - datetime.now(UTC)).total_seconds()
    except (TypeError, ValueError):
        remaining = -1
    need(0 < remaining <= 45, "Parent challenge expired")
    locked = scientific_scope_control._source_lock(full=True)
    need(
        locked["device"] == binding.get("lock_device")
        and locked["inode"] == binding.get("lock_inode")
        and locked["uid"] == binding.get("lease_uid")
        and locked["command_sha256"] == binding.get("lease_command_sha256")
        and locked["executable_sha256"] == binding.get("lease_executable_sha256")
        and socket.gethostname() == binding.get("hostname")
        and hashlib.sha256(Path("/etc/machine-id").read_bytes()).hexdigest()
        == binding.get("machine_sha256")
        and Path("/proc/sys/kernel/random/boot_id").read_text().strip() == binding.get("boot_id"),
        "Source lease identity differs",
    )
    ticks = os.sysconf("SC_CLK_TCK")
    need(type(ticks) is int and ticks > 0, "Source clock unavailable")
    deadline = min(time.monotonic() + min(remaining, 95), locked["start_ticks"] / ticks + 110)
    holder = {k: locked[k] for k in ("device", "inode", "pid", "uid", "start_ticks")}

    def check():
        checkpoint()
        scientific_capture._cgroup_limits()
        need(
            time.monotonic() < deadline
            and datetime.now(UTC) < expires
            and scientific_scope_control._source_lock(full=False) == holder,
            "Source lease expired or changed",
        )

    check()
    profile = Path("/etc/greenmind/delete-preparation/4de3e890632e.env")
    raw = _reader_profile_bytes(
        profile, "aa970526fcd0677587a101098fe42681704a72f4f9daab0ffdaa49ad2f37656f"
    )
    lines = [
        line.split("=", 1)[1]
        for line in raw.decode().splitlines()
        if line.startswith("RAW_ARCHIVE_GATEWAY_DATABASE_URL=")
    ]
    need(len(lines) == 1, "Existing Source reader required")
    url = make_url(lines[0])
    need(set(url.query) <= {"options"}, "Source reader options differ")
    url = url.set(
        drivername="postgresql+psycopg2",
        query={},
        database=parent_capture.DATABASES[request["source"]],
    )
    scientific_capture._engine_binding(type("Binding", (), {"url": url})(), url.database)
    before, _ = _observe_container()
    engine = create_engine(
        url,
        pool_size=1,
        max_overflow=0,
        connect_args={
            "connect_timeout": 3,
            "application_name": "gm-parent-snapshot-" + request["capture_nonce"],
            "options": "-c default_transaction_read_only=on -c statement_timeout=8000 -c lock_timeout=150 -c jit=off -c work_mem=2048",
        },
    )
    folder = Path("/mnt/HC_Volume_106755700") / (
        "greenmind-parent-snapshot-" + request["capture_nonce"]
    )
    try:
        check()
        folder.mkdir(mode=0o700)
        scientific_capture._source_resources(folder)
        factory = scientific_native.counter_factory(
            request["native_library"], request["native_sha256"]
        )
        result = parent_capture.capture(
            engine,
            request["source"],
            folder / "capture",
            checkpoint=check,
            lease_deadline=deadline,
            schema_master_sha256=request["reference_master_sha256"],
            provider_scope_sha256=request["provider_scope_sha256"],
            exported_snapshot=request["snapshot"],
            counter_factory=factory,
        )
        proof = result["capture"]
        need(
            proof["snapshot_sha256"] == request["visible_snapshot_sha256"]
            and proof["imported_snapshot_sha256"] == request["snapshot_sha256"],
            "Imported parent snapshot differs",
        )
        after, _ = _observe_container()
        need(before == after, "Source container generation changed")
        check()
        response = {
            "format": "greenmind.parent-snapshot-response.v1",
            "request_sha256": hashlib.sha256(canonical(request)).hexdigest(),
            "capture_root": str(folder / "capture"),
            "capture_sha256": result["capture_sha256"],
            "capture": proof,
            "source_lock": locked,
            "source_container_generation": before,
            "native_index_pending": True,
        }
        return encrypt_response(response, request)
    finally:
        engine.dispose()
