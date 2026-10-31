"""Publish complete bundles, independently restore, then bind a real snapshot."""

import json
import os
import tempfile
import wave
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from app.raw_archive.policy import ArchiveBlocked, checksum

from . import parquet
from .policy import POLICY_SHA256, utc


class SnapshotPending(ArchiveBlocked):
    """Published bytes need a newer snapshot before they can be verified."""


def candidate_json(candidate):
    return {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key, value in asdict(candidate).items()
    }


def key_for(candidate, sha, extension):
    return (
        f"{candidate.environment}/tiered/{candidate.organization}/{candidate.dataset}/"
        f"{candidate.start.date().isoformat()}/{sha}.{extension}"
    )


def verify_file(path, reference, checkpoint):
    if path.stat().st_size != reference["bytes"] or checksum(path) != reference["sha256"]:
        raise ArchiveBlocked("Bundle artifact differs from source")
    if reference["format"] == "parquet":
        parquet.verify(path, reference, checkpoint=checkpoint)
    elif reference["format"] == "wav":
        with wave.open(str(path), "rb") as wav:
            if "wav_header" in reference and reference["wav_header"] != {
                "frames": wav.getnframes(),
                "rate": wav.getframerate(),
                "channels": wav.getnchannels(),
                "width": wav.getsampwidth(),
            }:
                raise ArchiveBlocked("WAV sample count or format differs from source features")
            frames, width = wav.getnframes(), wav.getnchannels() * wav.getsampwidth()
            read = 0
            while body := wav.readframes(4096):
                checkpoint()
                read += len(body)
            if not frames or read != frames * width:
                raise ArchiveBlocked("Incomplete archived WAV PCM")


def publish(candidate, files, store, ledger, *, checkpoint, now=None):
    candidate.validate()
    if not files or len(files) > 128:
        raise ArchiveBlocked("Complete bounded artifact set required")
    now = now or datetime.now(UTC)
    previous = ledger.get(candidate.identity)
    if previous and previous.get("destination") != store.identity:
        raise ArchiveBlocked("Archive destination changed; retain original binding")
    if previous and previous["state"] in {"deleting", "evicted"}:
        return previous
    references = []
    for path, metadata in files:
        checkpoint()
        reference = dict(metadata)
        if reference["format"] not in {"parquet", "wav", "bin", "json", "gz"}:
            raise ArchiveBlocked("Unsupported archive artifact")
        verify_file(path, reference, checkpoint)
        reference["key"] = reference.get(
            "key", key_for(candidate, reference["sha256"], reference["format"])
        )
        store.publish(reference["key"], path)
        references.append(reference)
    if (
        references[0].get("content_sha256") != candidate.content_sha256
        or references[0].get("rows") != candidate.rows
    ):
        raise ArchiveBlocked("Source partition and primary archive differ")
    if previous and previous.get("files") != references:
        raise ArchiveBlocked("Changed archive encoding requires a separately versioned migration")
    return _publish_manifest(candidate, references, store, ledger, previous, checkpoint, now)


def _publish_manifest(candidate, references, store, ledger, previous, checkpoint, now):
    checkpoint()
    manifest = {
        "version": 1,
        "policy": POLICY_SHA256,
        "candidate": candidate_json(candidate),
        "identity": candidate.identity,
        "files": references,
    }
    with tempfile.TemporaryDirectory(dir=ledger.root) as folder:
        path = Path(folder) / "manifest.json"
        path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
        os.chmod(path, 0o600)
        ref = {
            "key": key_for(candidate, checksum(path), "json"),
            "sha256": checksum(path),
            "bytes": path.stat().st_size,
            "format": "json",
        }
        store.publish(ref["key"], path)
    return ledger.save(
        candidate,
        previous["state"] if previous else "published",
        {
            **(previous or {}),
            "manifest": ref,
            "files": references,
            "source": manifest["candidate"],
            "destination": store.identity,
            "published_at": previous["published_at"] if previous else now.isoformat(),
        },
        now,
    )


def complete_staged_wav(candidate, references, store, ledger, *, checkpoint, now=None):
    """Publish one whole revision only after independent readback of every staged file.

    Transfers into the staging journal are separately bounded per invocation;
    the normal publish() artifact ceiling remains unchanged.
    """
    from app.raw_archive.policy import Recording

    candidate.validate()
    if candidate.kind != "wav" or not 2 <= len(references) <= 1025:
        raise ArchiveBlocked("Complete bounded staged WAV revision required")
    if len({reference["key"] for reference in references}) != len(references):
        raise ArchiveBlocked("Duplicate artifact in staged WAV revision")
    previous = ledger.get(candidate.identity)
    if previous and previous.get("destination") != store.identity:
        raise ArchiveBlocked("Archive destination changed; retain original binding")
    if previous and previous["state"] in {"deleting", "evicted"}:
        return previous
    primary = references[0]
    if (
        primary.get("format") != "parquet"
        or primary.get("content_sha256") != candidate.content_sha256
        or primary.get("rows") != candidate.rows
        or candidate.rows != 1
    ):
        raise ArchiveBlocked("Staged WAV metadata differs from source")
    with tempfile.TemporaryDirectory(dir=ledger.root) as folder:
        path = Path(folder) / "returned"
        for index, reference in enumerate(references):
            checkpoint()
            store.download(reference["key"], path, reference["bytes"])
            verify_file(path, reference, checkpoint)
            if index == 0:
                rows = list(parquet.read(path, reference, checkpoint=checkpoint))
                if len(rows) != 1 or rows[0]["organization"] != candidate.organization:
                    raise ArchiveBlocked("Staged WAV ownership differs")
                originals = rows[0]["body"]["originals"]
                expected = [Recording.from_receipt(item["recording"]) for item in originals]
                if (
                    len(expected) != len(references) - 1
                    or any(record.kind != candidate.source for record in expected)
                    or [
                        (record.remote_key(candidate.environment), record.sha256, record.size)
                        for record in expected
                    ]
                    != [(ref["key"], ref["sha256"], ref["bytes"]) for ref in references[1:]]
                    or any(ref.get("format") != "wav" for ref in references[1:])
                    or sum(record.size for record in expected) > 64 * 1024**2
                ):
                    raise ArchiveBlocked("Staged WAV set is incomplete or changed")
            path.unlink()
    if previous and previous.get("files") != references:
        raise ArchiveBlocked("Changed archive encoding requires a separately versioned migration")
    return _publish_manifest(
        candidate, references, store, ledger, previous, checkpoint, now or datetime.now(UTC)
    )


def verify_snapshot(candidate, store, ledger, provider, snapshot_id, *, checkpoint, now=None):
    injected_clock = now is not None
    now = now or datetime.now(UTC)
    receipt = ledger.get(candidate.identity)
    if not receipt or receipt["state"] not in {"published", "verified", "deleting"}:
        raise ArchiveBlocked("Published bundle required before snapshot proof")
    if receipt.get("destination") != store.identity:
        raise ArchiveBlocked("Archive destination changed")
    checkpoint()
    snapshot = provider.read(snapshot_id)
    created = utc(snapshot["created"])
    if created > now:
        raise ArchiveBlocked("Snapshot proof is future-dated")
    if created < utc(receipt["published_at"]):
        raise SnapshotPending("Snapshot predates completed archive publication")
    with tempfile.TemporaryDirectory(dir=ledger.root) as folder:
        for number, reference in enumerate([receipt["manifest"], *receipt["files"]]):
            checkpoint()
            path = Path(folder) / str(number)
            store.download(reference["key"], path, reference["bytes"], snapshot=snapshot["name"])
            verify_file(path, reference, checkpoint)
            if number == 0:
                manifest = json.loads(path.read_text())
                if (
                    manifest["identity"] != candidate.identity
                    or manifest["files"] != receipt["files"]
                    or manifest["candidate"] != candidate_json(candidate)
                    or manifest["policy"] != POLICY_SHA256
                ):
                    raise ArchiveBlocked("Snapshot manifest does not describe this exact bundle")
            path.unlink()
    # Recheck provider identity after potentially long transfers, and timestamp
    # completion using a fresh clock rather than the time before the first GET.
    current = provider.read(snapshot_id)
    if any(
        current.get(key) != snapshot.get(key) for key in ("id", "storage_box", "name", "created")
    ):
        raise ArchiveBlocked("Snapshot identity changed during proof")
    finished = now if injected_clock else datetime.now(UTC)
    return ledger.save(
        candidate,
        "deleting" if receipt["state"] == "deleting" else "verified",
        dict(
            receipt,
            verified_at=finished.isoformat(),
            snapshot={
                "provider_id": snapshot["id"],
                "box_id": snapshot["storage_box"],
                "name": snapshot["name"],
                "created_at": created.isoformat(),
                "checked_at": finished.isoformat(),
            },
        ),
        finished,
    )
