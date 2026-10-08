"""Verified retrieval for future archive-aware API integration; not registered today."""

import hashlib
import json
import shutil
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from .policy import ArchiveBlocked, Recording, checksum


@contextmanager
def archived_file(config, destination, *, kind, bucket, key, scratch=None):
    """Read only the existing journal; never creates it or exposes unchecked bytes.

    Must be invoked behind existing sensor/company authorization. Callers retain
    the normal WAV filename and content type. No public Storage Box URLs or keys.
    """
    config.guard(reading=True)
    identity = json.dumps([kind, bucket, key], separators=(",", ":"))
    archive_id = hashlib.sha256(identity.encode()).hexdigest()
    journal = config.root / "archive.sqlite3"
    with closing(sqlite3.connect(journal.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
        row = db.execute("SELECT state, receipt FROM archive WHERE id=?", (archive_id,)).fetchone()
    if not row or row[0] not in {"verified", "deleting", "evicted"}:
        raise ArchiveBlocked("No verified archive receipt exists")
    receipt = json.loads(row[1])
    source = receipt["recording"]
    # Read compatibility for receipts from the earlier, never-deployed preparation.
    expected = (
        Recording.from_receipt(source).remote_key(config.namespace)
        if source.get("started_at") and source.get("sensor")
        else f"{config.namespace}/{kind}/{archive_id}/{source['sha256']}.wav"
    )
    if (
        receipt["destination"] != destination.identity
        or (source["kind"], source["bucket"], source["key"]) != (kind, bucket, key)
        or receipt["remote_key"] != expected
        or not 0 < source["size"] <= config.max_file_bytes
    ):
        raise ArchiveBlocked("Archive identity mismatch")
    scratch = scratch or config.root
    if shutil.disk_usage(scratch).free < config.min_free_bytes + source["size"]:
        raise ArchiveBlocked("Insufficient archive read scratch space")
    with TemporaryDirectory(prefix="restore-", dir=scratch) as folder:
        path = Path(folder) / "verified.wav"
        destination.download(
            receipt["remote_key"], path, min(source["size"], config.max_file_bytes)
        )
        if path.stat().st_size != source["size"] or checksum(path) != source["sha256"]:
            raise ArchiveBlocked("Archive readback mismatch")
        yield path
