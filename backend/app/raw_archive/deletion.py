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


def proposal(config, now):
    """Load a bounded technical proposal. It is never a human deletion approval."""
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
    if data.get("schema") != 2 or data.get("environment") != config.namespace:
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


def approval(config, now):
    data = proposal(config, now)
    consent = data.get("authorization", {})
    if (
        consent.get("approved") is not True
        or consent.get("action") != "bounded_wav_pilot"
        or not isinstance(consent.get("approved_by"), str)
        or not 1 <= len(consent["approved_by"].strip()) <= 128
    ):
        raise ArchiveBlocked("Separate explicit human pilot authorization is required")
    if not stamp(data["created_at"]) <= stamp(consent.get("approved_at")) <= now:
        raise ArchiveBlocked("Human pilot authorization has an invalid timestamp")
    return data


def verify_deletion_evidence(
    config, recording, receipt, destination, now, *, clock=None, scientific_source=None
):
    from .wav_context import require_deletable_receipt

    require_deletable_receipt(receipt)
    proof_at = _verify_evidence(
        config,
        recording,
        receipt,
        destination,
        now,
        approval(config, now),
        scientific_source=scientific_source,
    )
    # Four remote readbacks may outlive the consent or snapshot validity window.
    # Check the live deadline after their last byte, before any deletion.
    current = clock() if clock else now
    verify_current_authorization(config, recording, current, proof_at)
    return proof_at


def verify_current_authorization(config, recording, current, proof_at, *, consent=True):
    data = (approval if consent else proposal)(config, current)
    from .readiness import validate_release

    validate_release(data, recording, current)
    if current - stamp(data["snapshot"]["created_at"]) > timedelta(hours=24):
        raise ArchiveBlocked("Storage Box snapshot expired during proof readback")
    if current - proof_at > timedelta(hours=24):
        raise ArchiveBlocked("Catalog restore expired during proof readback")


def verify_preparation_evidence(
    config, recording, receipt, destination, now, *, clock=None, scientific_source=None
):
    """Same immutable proofs without consent; does not call any source adapter."""
    from .wav_context import require_deletable_receipt

    require_deletable_receipt(receipt)
    proof_at = _verify_evidence(
        config,
        recording,
        receipt,
        destination,
        now,
        proposal(config, now),
        scientific_source=scientific_source,
    )
    verify_current_authorization(
        config, recording, clock() if clock else now, proof_at, consent=False
    )


def _verify_evidence(config, recording, receipt, destination, now, data, *, scientific_source=None):
    check_quarantine(config, recording)
    if now < eligible_after(recording, receipt, config.local_grace_days):
        raise ArchiveBlocked("Seven-day local reserve has not elapsed")
    candidate = data["candidates"].get(recording.archive_id)
    expected = recording.content_identity() | {
        "remote_key": recording.remote_key(config.namespace),
        "feature_digest": recording.feature_digest,
        "source_snapshot": receipt["snapshot"],
    }
    if candidate != expected or data.get("destination") != destination.identity:
        raise ArchiveBlocked("File is outside the reviewed immutable deletion set")
    from .readiness import validate_release

    validate_release(data, recording, now)
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
            proof.get("schema") != 2
            or proof.get("environment") != config.namespace
            or proof.get("destination") != destination.identity
            or proof.get("deleted_files") != 0
        ):
            raise ArchiveBlocked("Catalog restoration proof identity mismatch")
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
        from .readiness import validate_metadata_restore

        validate_metadata_restore(catalog, proof)
        if recording.kind == "direct":
            candidate_proof = data.get("feature_v2", {}).get(recording.archive_id)
            if not isinstance(candidate_proof, dict):
                raise ArchiveBlocked("Scientific history proof is absent")
            descriptor = {
                key: value
                for key, value in candidate_proof.items()
                if key not in {"provider_snapshot", "checked_at", "restored_history_sha256"}
            }
            if (
                recovery.get("feature_v2", {}).get(recording.archive_id) != descriptor
                or proof.get("feature_v2", {}).get(recording.archive_id) != descriptor
            ):
                raise ArchiveBlocked("Scientific history descriptor is not snapshot bound")
            verify_feature_v2(
                candidate_proof,
                recording,
                destination,
                snapshot,
                now,
                Path(folder),
                scientific_source,
            )
        wav = Path(folder) / "snapshot.wav"
        destination.download_snapshot(name, receipt["remote_key"], wav, recording.size)
        if wav.stat().st_size != recording.size or checksum(wav) != recording.sha256:
            raise ArchiveBlocked("Snapshot WAV readback mismatch")
    return proof_at


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


def verify_feature_v2(proof, recording, destination, snapshot, now, folder, scientific_source):
    """Independently download every feature part from the pinned provider snapshot."""
    from .feature_backup import (
        FORMAT_DIRECT_REVISION,
        check_manifest,
        compare_restored_direct_history,
        restore,
    )

    if (
        not callable(scientific_source)
        or not isinstance(proof, dict)
        or set(proof)
        != {
            "format",
            "source",
            "revision",
            "manifest_sha256",
            "history_sha256",
            "restored_history_sha256",
            "checked_at",
            "provider_snapshot",
            "manifest",
            "parts",
        }
    ):
        raise ArchiveBlocked("Exact scientific history proof and fresh source are required")
    snapshot_binding = {
        "provider_id": snapshot.get("provider_id"),
        "box_id": snapshot.get("box_id", snapshot.get("storage_box_id")),
        "name": snapshot.get("name"),
        "created_at": snapshot.get("created_at"),
    }
    if (
        type(snapshot_binding["provider_id"]) is not int
        or snapshot_binding["provider_id"] <= 0
        or type(snapshot_binding["box_id"]) is not int
        or snapshot_binding["box_id"] <= 0
        or not isinstance(snapshot_binding["name"], str)
        or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", snapshot_binding["name"])
    ):
        raise ArchiveBlocked("Invalid provider snapshot identity")
    if (
        proof["format"] != "direct-feature-proof-v2"
        or proof["source"] != "direct"
        or str(proof["revision"]) != recording.identity
        or proof["manifest_sha256"] != recording.feature_digest
        or proof["provider_snapshot"] != snapshot_binding
        or proof["history_sha256"] != proof["restored_history_sha256"]
        or not re.fullmatch("[0-9a-f]{64}", proof["history_sha256"])
    ):
        raise ArchiveBlocked("Scientific proof source or provider snapshot mismatch")
    checked = stamp(proof["checked_at"])
    if not now - timedelta(hours=24) <= stamp(snapshot["created_at"]) <= checked <= now:
        raise ArchiveBlocked("Scientific snapshot or readback proof is stale")
    reference = proof["manifest"]
    if (
        not isinstance(reference, dict)
        or set(reference) != {"key", "sha256", "size"}
        or not re.fullmatch("[0-9a-f]{64}", reference["sha256"])
        or type(reference["size"]) is not int
        or not 0 < reference["size"] <= 1024**2
        or reference["key"] != f"production/catalog-backup/{reference['sha256']}.json"
    ):
        raise ArchiveBlocked("Invalid feature manifest reference")
    before, revision = scientific_source(recording)
    if before["sha256"] != proof["history_sha256"]:
        raise ArchiveBlocked("Current scientific history differs from reviewed proof")
    manifest_path = folder / "feature-v2-manifest.json"
    destination.download_catalog(
        reference["key"], manifest_path, reference["size"], snapshot=snapshot["name"]
    )
    if (
        manifest_path.stat().st_size != reference["size"]
        or checksum(manifest_path) != reference["sha256"]
    ):
        raise ArchiveBlocked("Snapshot feature manifest readback mismatch")
    manifest = json.loads(manifest_path.read_text())
    check_manifest(manifest)
    if (
        manifest["format"] != FORMAT_DIRECT_REVISION
        or manifest["destination"] != destination.identity
        or manifest.get("scientific_coverage")
        != {key: revision[key] for key in ("segment_id", "revision", "manifest_sha256")}
    ):
        raise ArchiveBlocked("Snapshot feature format, source scope or destination differs")
    expected = [
        {
            "file": entry["file"],
            "key": entry.get("remote_key"),
            "size": entry["bytes"],
            "sha256": entry["sha256"],
        }
        for entry in manifest["files"]
    ]
    if proof["parts"] != expected:
        raise ArchiveBlocked("Feature proof parts differ from manifest")
    files = {}
    for entry in expected:
        if entry["key"] != f"production/catalog-backup/{entry['sha256']}.jsonl.gz":
            raise ArchiveBlocked("Unapproved feature part key")
        path = folder / entry["file"]
        destination.download_catalog(entry["key"], path, entry["size"], snapshot=snapshot["name"])
        if path.stat().st_size != entry["size"] or checksum(path) != entry["sha256"]:
            raise ArchiveBlocked("Snapshot feature part readback mismatch")
        files[entry["file"]] = path
    restored = folder / "feature-v2.sqlite3"
    restore(manifest, files, restored)
    if (
        compare_restored_direct_history(restored, before, revision)
        != proof["restored_history_sha256"]
    ):
        raise ArchiveBlocked("Scientific history typed readback differs")
    after, after_revision = scientific_source(recording)
    if after != before or after_revision != revision:
        raise ArchiveBlocked("Scientific source changed during snapshot readback")
    return proof["history_sha256"]
