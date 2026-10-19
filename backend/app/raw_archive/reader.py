"""Opt-in verified S3-miss fallback. Existing authentication stays with the caller."""

import hashlib
import json
import os
import sqlite3
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import BoundedSemaphore

from botocore.exceptions import ClientError

_read_slots = BoundedSemaphore(2)


def verified_objects(kind, bucket, keys):
    """Read-only availability check for a bounded, authorized listing."""
    if os.environ.get("RAW_ARCHIVE_READS_ENABLED", "false").lower() != "true" or len(keys) > 1000:
        return set()
    if os.environ.get("RAW_ARCHIVE_READ_BROKER_SOCKET"):
        from .broker_client import available

        return available(kind, bucket, keys) if keys else set()
    root = Path(os.environ.get("RAW_ARCHIVE_STATE_DIR", "/var/lib/greenmind-raw-archive"))
    path = root / "archive.sqlite3"
    if not path.is_file():
        return set()
    result = set()
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2) as db:
        for key in keys:
            identity = json.dumps([kind, bucket, key], separators=(",", ":"))
            archive_id = hashlib.sha256(identity.encode()).hexdigest()
            row = db.execute("SELECT state FROM archive WHERE id=?", (archive_id,)).fetchone()
            if row and row[0] in {"verified", "deleting", "evicted"}:
                result.add(key)
    return result


def should_restore(error, key):
    return (
        os.environ.get("RAW_ARCHIVE_READS_ENABLED", "false").lower() == "true"
        and key.endswith(".wav")
        and isinstance(error, ClientError)
        and error.response.get("Error", {}).get("Code") in {"NoSuchKey", "404", "NotFound"}
    )


class VerifiedBody:
    """Keep verified temporary bytes alive until the streaming consumer closes them."""

    def __init__(self, file, resources):
        self.file, self.resources = file, resources

    def read(self, size=-1):
        return self.file.read(size)

    def close(self):
        self.resources.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def restore_object(*, kind, bucket, key):
    from .policy import ArchiveBlocked
    from .restore import archived_file
    from .runner import destination_from_environment
    from .worker import configuration

    if os.environ.get("RAW_ARCHIVE_READS_ENABLED", "false").lower() != "true":
        raise ArchiveBlocked("Archive reads are disabled")
    config = configuration()
    resources = ExitStack()
    try:
        if not _read_slots.acquire(blocking=False):
            raise ArchiveBlocked("Archive read concurrency limit reached; retry later")
        resources.callback(_read_slots.release)
        scratch_setting = os.environ.get("RAW_ARCHIVE_READ_SCRATCH_DIR")
        if scratch_setting:
            scratch = Path(scratch_setting)
            if (
                not scratch.is_absolute()
                or scratch.is_symlink()
                or not scratch.is_dir()
                or scratch.stat().st_uid != os.getuid()
                or scratch.stat().st_mode & 0o077
            ):
                raise ArchiveBlocked("Private existing archive read scratch directory required")
        else:
            scratch = Path(resources.enter_context(TemporaryDirectory(prefix="greenmind-archive-")))
        if os.environ.get("RAW_ARCHIVE_READ_BROKER_SOCKET"):
            from .broker_client import restore

            path = restore(resources, kind=kind, bucket=bucket, key=key, scratch=scratch)
        else:
            path = resources.enter_context(
                archived_file(
                    config,
                    destination_from_environment(),
                    kind=kind,
                    bucket=bucket,
                    key=key,
                    scratch=scratch,
                )
            )
        size = path.stat().st_size
        file = resources.enter_context(path.open("rb"))
        return {
            "Body": VerifiedBody(file, resources),
            "ContentLength": size,
            "ContentType": "audio/wav",
            "ArchiveVerified": True,
        }
    except BaseException:
        resources.close()
        raise


def get_object(client, *, Bucket, Key, kind):
    try:
        return client.get_object(Bucket=Bucket, Key=Key)
    except ClientError as error:
        if not should_restore(error, Key):
            raise
        return restore_object(kind=kind, bucket=Bucket, key=Key)
