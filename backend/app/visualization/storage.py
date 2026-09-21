"""Append-only, verified source backups on a separate filesystem."""

import gzip
import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path


def write_archive(root: Path, records, label: str, *, progress=None):
    """Create a durable gzip JSONL snapshot, then independently read it back."""
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if shutil.disk_usage(root).free < 2 * 1024**3:
        raise RuntimeError("Backup filesystem has less than 2 GiB free")
    target = root / f"{label}-{uuid.uuid4().hex}.jsonl.gz"
    pending = target.with_suffix(".partial")
    count = 0
    digest = hashlib.sha256()
    with pending.open("xb") as raw:
        os.chmod(pending, 0o600)
        with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=3, mtime=0) as output:
            for record in records:
                line = (
                    json.dumps(dict(record), default=str, sort_keys=True, separators=(",", ":"))
                    + "\n"
                ).encode()
                output.write(line)
                digest.update(line)
                count += 1
                if progress and count % 1000 == 0:
                    progress("archive_write", count)
        raw.flush()
        os.fsync(raw.fileno())
    verified = hashlib.sha256()
    verified_count = 0
    with gzip.open(pending, "rb") as archived:
        for line in archived:
            json.loads(line)
            verified.update(line)
            verified_count += 1
            if progress and verified_count % 1000 == 0:
                progress("archive_verify", verified_count)
    if (verified.hexdigest(), verified_count) != (digest.hexdigest(), count):
        raise RuntimeError("Source archive verification failed")
    pending.rename(target)
    descriptor = os.open(root, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return {
        "path": str(target),
        "sha256": digest.hexdigest(),
        "rows": count,
        "bytes": target.stat().st_size,
    }


def read_archive(manifest):
    digest = hashlib.sha256()
    count = 0
    with gzip.open(manifest["path"], "rb") as archived:
        for line in archived:
            digest.update(line)
            count += 1
            yield json.loads(line)
    if digest.hexdigest() != manifest["sha256"] or count != manifest["rows"]:
        raise RuntimeError("Source archive checksum mismatch")
