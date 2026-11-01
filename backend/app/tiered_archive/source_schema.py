"""Narrow schema-only hook for an independently admitted private Root transport.

This module creates no SSH channel and reads no Root credentials. The existing
text admin probe is deliberately not accepted as a schema-stream transport.
A caller must supply its separately reviewed private request/response binding.
"""

import hashlib
import json
import os
import re
import stat
from pathlib import Path

from app.raw_archive.policy import ArchiveBlocked

from . import schema_provider
from .codec import digest

SOURCE_MACHINE = "065c65fd7f52297f59a00eddc22986c3b0c1530aa7e12b99e9d4d88c1155dd58"
SOURCE_CONTAINER = "fa5679cd84c16b5024251bb1f93414201fb091900af590cb63a7ecc087dddb71"
# The native source inventory, not a container-name guess, must bind this ID.
SNAPSHOT = re.compile(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{8}-[0-9A-Fa-f]{1,16}\Z")
SHA = re.compile(r"[a-f0-9]{64}\Z")
ROOT_UID = 0
MAX_BYTES = 64 * 1024**2


def need(value, message):
    if not value:
        raise ArchiveBlocked(message)


def identity(info):
    return tuple(
        getattr(info, key)
        for key in (
            "st_dev",
            "st_ino",
            "st_uid",
            "st_gid",
            "st_mode",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
    )


def private_directory(path):
    path = Path(path)
    need(path.is_absolute() and path.resolve() == path, "Exact private schema directory required")
    info = path.stat()
    need(
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == ROOT_UID
        and stat.S_IMODE(info.st_mode) == 0o700,
        "Root-private schema directory required",
    )
    return path


def private_read(path, maximum, expected=None):
    private_directory(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        need(
            stat.S_ISREG(before.st_mode)
            and before.st_uid == ROOT_UID
            and before.st_nlink == 1
            and stat.S_IMODE(before.st_mode) in {0o400, 0o600}
            and 0 < before.st_size <= maximum,
            "Private schema response file required",
        )
        raw = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    need(
        len(raw) == before.st_size
        and identity(before) == identity(after) == identity(path.lstat())
        and (expected is None or hashlib.sha256(raw).hexdigest() == expected),
        "Private schema response changed",
    )
    return raw


class SourceSchemaCapture:
    """Consume only a pinned private response, never stdout or a text relay.

    `private_response(request, checkpoint=...)` is supplied by the existing admitted
    Root supervisor. It returns (private_directory, receipt_sha256), after that
    supervisor's OWN process wait and source-identity checks. No implementation or
    claim of that not-yet-bound production transport is hidden in this adapter.
    """

    def __init__(
        self,
        scope,
        *,
        private_response=None,
        source_program_sha256=None,
        transport_binding_sha256=None,
    ):
        self.scope = scope
        self.private_response = private_response
        self.source_program_sha256 = source_program_sha256
        self.transport_binding_sha256 = transport_binding_sha256

    def require_scope(self, scope):
        need(os.geteuid() == ROOT_UID, "Root-only source schema capture required")
        schema_provider.validate_scope(scope)
        need(
            scope == self.scope
            and scope["environment"] == "production"
            and scope["source"] == "gateway"
            and scope["database"] == "plantdb",
            "Exact production Gateway provider scope required",
        )
        need(
            callable(self.private_response)
            and isinstance(self.source_program_sha256, str)
            and SHA.fullmatch(self.source_program_sha256)
            and isinstance(self.transport_binding_sha256, str)
            and SHA.fullmatch(self.transport_binding_sha256),
            "Existing admitted private Root schema transport is not bound",
        )

    def capture(self, *, db, source, snapshot, target, checkpoint):
        self.require_scope(self.scope)
        need(
            source == "gateway" and isinstance(snapshot, str) and SNAPSHOT.fullmatch(snapshot),
            "Exact exported PostgreSQL snapshot required",
        )
        from sqlalchemy import text

        isolation, read_only = db.execute(
            text(
                "SELECT current_setting('transaction_isolation'),current_setting('transaction_read_only')"
            )
        ).one()
        need(
            db.in_transaction() and isolation == "repeatable read" and read_only == "on",
            "Schema must share the live read-only RR capture transaction",
        )
        # Reuse the same authoritative physical-source/role/OID/filter fence,
        # including when this adapter is called outside backup.dump.
        schema_provider.capture_provider(db, self.scope, checkpoint=checkpoint)
        target = Path(target)
        private_directory(target.parent)
        request = {
            "format": "greenmind.private-source-schema-request.v1",
            "scope_sha256": digest(self.scope),
            "source_identity_sha256": self.scope["source_identity_sha256"],
            "source_machine_sha256": SOURCE_MACHINE,
            "source_container": SOURCE_CONTAINER,
            "database_oid": 16384,
            "server_version_num": 150010,
            "snapshot": snapshot,
            "program_sha256": self.source_program_sha256,
            "transport_binding_sha256": self.transport_binding_sha256,
            # Source Root resolves the EXISTING local admin only in that fixed
            # container. No username/password, backup key or shell is relayed.
            "pg_dump_args": [
                "--no-password",
                "--format=custom",
                "--schema-only",
                "--no-owner",
                "--no-acl",
                "--no-blobs",
                "--snapshot=" + snapshot,
                "--dbname=plantdb",
            ],
            "max_bytes": MAX_BYTES,
            "application_data_authorized": False,
        }
        checkpoint()
        folder, receipt_sha = self.private_response(request, checkpoint=checkpoint)
        need(
            isinstance(receipt_sha, str) and SHA.fullmatch(receipt_sha),
            "Pinned private response required",
        )
        folder = private_directory(folder)
        receipt = json.loads(private_read(folder / "response.json", 32768, receipt_sha))
        need(
            isinstance(receipt, dict)
            and set(receipt)
            == {"format", "request_sha256", "owned_wait_exit", "source_unchanged", "schema"}
            and receipt["format"] == "greenmind.private-source-schema-response.v1"
            and receipt["request_sha256"] == digest(request)
            and type(receipt["owned_wait_exit"]) is int
            and receipt["owned_wait_exit"] == 0
            and receipt["source_unchanged"] is True,
            "Source schema response lacks exact request and terminal proof",
        )
        item = receipt["schema"]
        need(
            isinstance(item, dict)
            and set(item) == {"name", "bytes", "sha256"}
            and item["name"] == "schema.dump"
            and type(item["bytes"]) is int
            and 5 <= item["bytes"] <= MAX_BYTES
            and isinstance(item["sha256"], str)
            and SHA.fullmatch(item["sha256"]),
            "Bounded schema-only response descriptor required",
        )
        # Stream a checked private file; never load a whole schema dump in RAM.
        fd = os.open(folder / "schema.dump", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as incoming:
            before = os.fstat(incoming.fileno())
            need(
                stat.S_ISREG(before.st_mode)
                and before.st_uid == ROOT_UID
                and before.st_nlink == 1
                and stat.S_IMODE(before.st_mode) in {0o400, 0o600}
                and before.st_size == item["bytes"],
                "Exact private schema stream required",
            )
            out = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            h = hashlib.sha256()
            total = 0
            with os.fdopen(out, "wb") as destination:
                while block := incoming.read(min(1024**2, item["bytes"] - total + 1)):
                    checkpoint()
                    total += len(block)
                    need(total <= item["bytes"], "Schema stream grew beyond bound")
                    if total == len(block):
                        need(block.startswith(b"PGDMP"), "Custom schema archive required")
                    destination.write(block)
                    h.update(block)
                destination.flush()
                os.fsync(destination.fileno())
            need(
                identity(before)
                == identity(os.fstat(incoming.fileno()))
                == identity((folder / "schema.dump").lstat())
                and total == item["bytes"]
                and h.hexdigest() == item["sha256"],
                "Complete schema stream identity differs",
            )
        checkpoint()
        # Reuse the existing pinned PG15 client and complete provider TOC fence.
        # Only schema definitions are authorized. Masters/provider stay in the
        # caller's existing capture, under the very SAME exported snapshot.
        from . import backup
        from .database_restore import run_client

        toc_path = target.with_suffix(".source-schema.toc")
        toc_path.touch(mode=0o600, exist_ok=False)
        run_client(
            [
                *backup.client_command("pg_restore"),
                "--list",
                "--file=" + str(toc_path),
                str(target),
            ],
            {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
            checkpoint=checkpoint,
            output=toc_path,
        )
        raw = private_read(toc_path, schema_provider.MAX_TOC_BYTES)
        schema_provider.validate_toc(raw, self.scope, "schema", ())
        return {"request_sha256": digest(request), "schema_sha256": h.hexdigest(), "bytes": total}
