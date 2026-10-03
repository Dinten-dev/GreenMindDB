"""Per-file deletion gates. This module never grants consent or deletes an object."""

import hashlib
import json
import os
import re
import tempfile
from datetime import timedelta

from .policy import ArchiveBlocked, checksum


def stamp(value):
    from datetime import datetime

    try:
        parsed = datetime.fromisoformat(value)
    except (ValueError, TypeError) as error:
        raise ArchiveBlocked("Missing or invalid verification timestamp") from error
    if parsed.tzinfo is None:
        raise ArchiveBlocked("Verification timestamps must include a timezone")
    return parsed


def eligible_after(recording, receipt, days=7):
    if not 7 <= days <= 365:
        raise ArchiveBlocked("At least seven days of local reserve are required")
    first = stamp(receipt.get("first_verified_at", receipt.get("verified_at")))
    return max(recording.ended_at, recording.received_at, first) + timedelta(days=days)


def approval(config, now):
    path = config.deletion_approval_file
    if path is None or not path.is_absolute() or path.is_symlink():
        raise ArchiveBlocked("Reviewed deletion manifest is required")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        raise ArchiveBlocked("Reviewed deletion manifest is unavailable") from error
    with os.fdopen(descriptor, "rb") as body:
        info = os.fstat(body.fileno())
        if info.st_uid not in {0, os.getuid()} or info.st_mode & 0o077 or info.st_size > 1024**2:
            raise ArchiveBlocked("Deletion manifest must be private and bounded")
        raw = body.read(1024**2 + 1)
    if not re.fullmatch("[0-9a-f]{64}", config.deletion_approval_sha256):
        raise ArchiveBlocked("Pin the reviewed deletion manifest checksum")
    if len(raw) > 1024**2 or hashlib.sha256(raw).hexdigest() != config.deletion_approval_sha256:
        raise ArchiveBlocked("Reviewed deletion manifest changed")
    data = json.loads(raw)
    created, expires = stamp(data.get("created_at")), stamp(data.get("expires_at"))
    if not created <= now < expires <= created + timedelta(hours=24):
        raise ArchiveBlocked("Deletion manifest expired or has an invalid validity window")
    if data.get("schema") != 1 or data.get("environment") != config.namespace:
        raise ArchiveBlocked("Wrong deletion manifest schema or environment")
    candidates = data.get("candidates")
    if not isinstance(candidates, dict) or not 1 <= len(candidates) <= 10:
        raise ArchiveBlocked("Initial deletion pilot is limited to ten files")
    if any(
        not isinstance(item, dict) or type(item.get("size")) is not int or item["size"] <= 0
        for item in candidates.values()
    ):
        raise ArchiveBlocked("Deletion candidates need positive byte lengths")
    if sum(item.get("size", 0) for item in candidates.values()) > 5 * 1024**2:
        raise ArchiveBlocked("Initial deletion pilot is limited to five MiB")
    return data


def verify_deletion_evidence(config, recording, receipt, destination, now):
    check_quarantine(config, recording)
    if now < eligible_after(recording, receipt, config.local_grace_days):
        raise ArchiveBlocked("Seven-day local reserve has not elapsed")
    data = approval(config, now)
    candidate = data["candidates"].get(recording.archive_id)
    expected = recording.content_identity() | {
        "remote_key": recording.remote_key(config.namespace),
        "feature_digest": recording.feature_digest,
        "source_snapshot": receipt["snapshot"],
    }
    if candidate != expected or data.get("destination") != destination.identity:
        raise ArchiveBlocked("File is outside the reviewed immutable deletion set")
    if (
        receipt["snapshot"].get("VersionId") in {None, "", "null"}
        and data.get("legacy_null_accepted") is not True
    ):
        raise ArchiveBlocked("Legacy null versions require deployed-MinIO test acceptance")
    snapshot = data.get("snapshot", {})
    name = snapshot.get("name", "")
    snapshot_at = stamp(snapshot.get("created_at"))
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", name):
        raise ArchiveBlocked("Invalid Storage Box snapshot identity")
    if not stamp(receipt["first_verified_at"]) <= snapshot_at <= now:
        raise ArchiveBlocked("Snapshot predates the verified archive file")
    if now - snapshot_at > timedelta(hours=24):
        raise ArchiveBlocked("A recent Storage Box snapshot is required")
    index = data.get("recovery_index", {})
    if not re.fullmatch("[0-9a-f]{64}", index.get("sha256", "")):
        raise ArchiveBlocked("Recovery index checksum is required")
    if not 0 < index.get("size", 0) <= 1024**2:
        raise ArchiveBlocked("Recovery index must be bounded")
    # Both bodies are retrieved again from the snapshot; a file listing is
    # insufficient, and a provider account outage must fail closed.
    with tempfile.TemporaryDirectory(prefix="delete-proof-", dir=config.root) as folder:
        from pathlib import Path

        index_path = Path(folder) / "index.json"
        destination.download_recovery(index["key"], index_path, index["size"], snapshot=name)
        if index_path.stat().st_size != index["size"] or checksum(index_path) != index["sha256"]:
            raise ArchiveBlocked("Snapshot recovery index readback mismatch")
        recovery = json.loads(index_path.read_text())
        if (
            recovery.get("environment") != config.namespace
            or recovery.get("destination") != destination.identity
        ):
            raise ArchiveBlocked("Recovery index belongs to another environment or archive")
        if recovery.get("candidates", {}).get(recording.archive_id) != expected:
            raise ArchiveBlocked("Snapshot recovery index does not cover this file")
        saved_recording = recovery.get("recordings", {}).get(recording.archive_id)
        if not saved_recording or any(
            saved_recording.get(key) != value for key, value in recording.content_identity().items()
        ):
            raise ArchiveBlocked("Snapshot index lacks recoverable recording metadata")
        proof_ref = data.get("catalog_restore", {})
        if (
            not re.fullmatch("[0-9a-f]{64}", proof_ref.get("sha256", ""))
            or not 0 < proof_ref.get("size", 0) <= 1024**2
        ):
            raise ArchiveBlocked("Offsite catalog restoration proof is required")
        proof_path = Path(folder) / "catalog-restore.json"
        destination.download_catalog(proof_ref["key"], proof_path, proof_ref["size"], snapshot=name)
        if (
            proof_path.stat().st_size != proof_ref["size"]
            or checksum(proof_path) != proof_ref["sha256"]
        ):
            raise ArchiveBlocked("Catalog restoration proof readback mismatch")
        proof = json.loads(proof_path.read_text())
        if (
            proof.get("schema") != 1
            or proof.get("environment") != config.namespace
            or proof.get("destination") != destination.identity
            or proof.get("deleted_files") != 0
        ):
            raise ArchiveBlocked("Catalog restoration proof identity mismatch")
        restored = proof.get("restored", [])
        if (
            len(restored) != 3
            or {item.get("kind") for item in restored} != {"ledger", "gateway", "direct"}
            or any(type(item.get("rows")) is not int or item["rows"] <= 0 for item in restored)
        ):
            raise ArchiveBlocked("Complete journal and catalog restore is required")
        proof_at = stamp(proof.get("created_at"))
        if not now - timedelta(hours=24) <= proof_at <= snapshot_at:
            raise ArchiveBlocked("Catalog restore must be recent and included in snapshot")
        reference = proof.get("catalog_manifest", {})
        if reference != data.get("catalog_manifest") or not re.fullmatch(
            "[0-9a-f]{64}", reference.get("sha256", "")
        ):
            raise ArchiveBlocked("Reviewed catalog backup differs from restoration proof")
        if not 0 < reference.get("size", 0) <= 1024**2:
            raise ArchiveBlocked("Catalog manifest exceeds its size budget")
        catalog_path = Path(folder) / "catalog-manifest.json"
        destination.download_catalog(
            reference["key"], catalog_path, reference["size"], snapshot=name
        )
        if (
            catalog_path.stat().st_size != reference["size"]
            or checksum(catalog_path) != reference["sha256"]
        ):
            raise ArchiveBlocked("Snapshot catalog manifest readback mismatch")
        catalog = json.loads(catalog_path.read_text())
        if (
            catalog.get("environment") != config.namespace
            or catalog.get("destination") != destination.identity
            or not stamp(receipt["first_verified_at"])
            <= stamp(catalog.get("created_at"))
            <= proof_at
        ):
            raise ArchiveBlocked("Catalog backup predates the verified recording")
        files = catalog.get("files", [])
        if len(files) != 3:
            raise ArchiveBlocked("Catalog manifest does not cover all three sources")
        expected_restore = {(item.get("sha256"), item.get("rows")) for item in files}
        if expected_restore != {(item.get("sha256"), item.get("rows")) for item in restored}:
            raise ArchiveBlocked("Restored rows and hashes differ from catalog manifest")
        wav = Path(folder) / "snapshot.wav"
        destination.download_snapshot(name, receipt["remote_key"], wav, recording.size)
        if wav.stat().st_size != recording.size or checksum(wav) != recording.sha256:
            raise ArchiveBlocked("Snapshot WAV readback mismatch")


def check_quarantine(config, recording):
    """A configured exclusion register must remain readable; never silently ignore it."""
    path = config.quarantine_file
    if path is None:
        return
    if not path.is_absolute() or path.is_symlink():
        raise ArchiveBlocked("Private quarantine register required")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as body:
            info = os.fstat(body.fileno())
            if (
                info.st_uid not in {0, os.getuid()}
                or info.st_mode & 0o077
                or info.st_size > 1024**2
            ):
                raise ArchiveBlocked("Quarantine register must be private and bounded")
            data = json.loads(body.read(1024**2 + 1))
        if data.get("schema") != 1 or data.get("environment") != config.namespace:
            raise ArchiveBlocked("Quarantine identity mismatch")
        entries = data["excluded"]
        if not isinstance(entries, list) or any(
            not isinstance(row, dict)
            or row.get("kind") not in {"gateway", "direct"}
            or not isinstance(row.get("identity"), str)
            or not row["identity"]
            for row in entries
        ):
            raise ArchiveBlocked("Invalid quarantine entries")
        if any(
            row["kind"] == recording.kind and row["identity"] == recording.identity
            for row in entries
        ):
            raise ArchiveBlocked("Recording is quarantined; original retained")
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ArchiveBlocked("Quarantine register is unavailable or invalid") from error
