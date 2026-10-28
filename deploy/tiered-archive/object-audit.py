"""Bounded, read-only body verification; never publish or delete an S3 object."""

import hashlib
import json
import wave
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from app.raw_archive.policy import ArchiveBlocked, checksum
from app.raw_archive.storage import S3Source, object_headers

MAX_VERSIONS = 1024
MAX_BYTES = 16 * 1024**2


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def inventory(client, bucket, checkpoint):
    versions, markers, seen, cursors = [], [], set(), set()
    cursor = {}
    while True:
        checkpoint()
        page = client.list_object_versions(Bucket=bucket, MaxKeys=128, **cursor)
        for name, target in (("Versions", versions), ("DeleteMarkers", markers)):
            for row in page.get(name, []):
                identity = row["Key"], row["VersionId"]
                if identity in seen:
                    raise ArchiveBlocked("Duplicate version in source inventory")
                seen.add(identity)
                target.append(
                    {
                        k: row[k]
                        for k in (
                            "Key",
                            "VersionId",
                            "IsLatest",
                            "LastModified",
                            "ETag",
                            "Size",
                            "StorageClass",
                        )
                        if k in row
                    }
                )
                if len(seen) > MAX_VERSIONS:
                    raise ArchiveBlocked("Source inventory exceeds version budget")
        if sum(row["Size"] for row in versions) > MAX_BYTES:
            raise ArchiveBlocked("Source inventory exceeds body budget")
        if not page.get("IsTruncated"):
            break
        next_cursor = page.get("NextKeyMarker"), page.get("NextVersionIdMarker")
        if (
            not isinstance(next_cursor[0], str)
            or not next_cursor[0]
            or not isinstance(next_cursor[1], str)
        ) or next_cursor in cursors:
            raise ArchiveBlocked("Source inventory continuation did not advance")
        cursors.add(next_cursor)
        cursor = {"KeyMarker": next_cursor[0], "VersionIdMarker": next_cursor[1]}
    return {
        "versions": sorted(versions, key=lambda v: (v["Key"], v["VersionId"])),
        "delete_markers": sorted(markers, key=lambda v: (v["Key"], v["VersionId"])),
    }


def decode_wav(path, checkpoint):
    with wave.open(str(path), "rb") as wav:
        frames = wav.getnframes()
        width = wav.getnchannels() * wav.getsampwidth()
        count = 0
        while block := wav.readframes(8192):
            checkpoint()
            count += len(block)
        if not frames or count != frames * width:
            raise ArchiveBlocked("Incomplete WAV PCM")
        return {
            "frames": frames,
            "rate": wav.getframerate(),
            "channels": wav.getnchannels(),
            "width": wav.getsampwidth(),
            "decoded_bytes": count,
        }


def audit_bucket(client, bucket, scratch, *, checkpoint, progress=lambda _: None):
    scratch = Path(scratch)
    if not scratch.is_dir() or any(scratch.iterdir()) or scratch.is_symlink():
        raise ArchiveBlocked(
            "New empty private object-audit scratch directory required"
        )
    first = inventory(client, bucket, checkpoint)
    versioning = client.get_bucket_versioning(Bucket=bucket).get(
        "Status", "not_enabled"
    )
    result = {
        "bucket": bucket,
        "inventory_sha256": digest(first),
        "inventory": first,
        "versioning": versioning,
        "objects": [],
        "passed": False,
        "read_bytes": 0,
    }
    adapter = S3Source(client)
    for index, row in enumerate(first["versions"]):
        checkpoint()
        recording = SimpleNamespace(bucket=bucket, key=row["Key"], size=row["Size"])
        head = client.head_object(
            Bucket=bucket, Key=row["Key"], VersionId=row["VersionId"]
        )
        proof = {key: head.get(key) for key in ("VersionId", "ETag", "ContentLength")}
        proof.update(
            LastModified=head["LastModified"].isoformat(),
            ObjectHeaders=object_headers(head),
        )
        if proof["ContentLength"] != recording.size:
            raise ArchiveBlocked("Source size changed since inventory")
        if (
            (proof.get("VersionId") or "null") != (row["VersionId"] or "null")
            or proof["ETag"] != row["ETag"]
            or datetime.fromisoformat(proof["LastModified"])
            .astimezone(UTC)
            .replace(microsecond=0)
            != row["LastModified"].astimezone(UTC).replace(microsecond=0)
        ):
            raise ArchiveBlocked("Source version changed since inventory")
        path = scratch / f"{index:04}.wav"
        try:
            adapter.download(recording, proof, path)
            checkpoint()
            record = {
                "key": row["Key"],
                "version_id": row["VersionId"],
                "bytes": path.stat().st_size,
                "sha256": checksum(path),
                "snapshot_sha256": digest(proof),
                "stable_read": True,
                "listed_last_modified": row["LastModified"].isoformat(),
                "head_last_modified": proof["LastModified"],
            }
            try:
                record["wav"] = decode_wav(path, checkpoint)
                record["decodable"] = True
            except (wave.Error, EOFError, ArchiveBlocked) as error:
                # Health/deadline pauses must propagate; do not call them damaged audio.
                if (
                    isinstance(error, ArchiveBlocked)
                    and str(error) != "Incomplete WAV PCM"
                ):
                    raise
                record["decodable"] = False
                record["content_error"] = type(error).__name__
            result["read_bytes"] += record["bytes"]
            result["objects"].append(record)
            progress(record)
        finally:
            if path.exists():
                path.unlink()
    checkpoint()
    if digest(inventory(client, bucket, checkpoint)) != result["inventory_sha256"]:
        raise ArchiveBlocked("Source inventory changed during full read")
    if (
        client.get_bucket_versioning(Bucket=bucket).get("Status", "not_enabled")
        != versioning
    ):
        raise ArchiveBlocked("Source versioning changed during full read")
    result["stable_inventory"] = True
    result["passed"] = not first["delete_markers"] and all(
        v["decodable"] for v in result["objects"]
    )
    return result
