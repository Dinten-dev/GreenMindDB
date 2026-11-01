"""Versioned immutable Direct feature-history attachment for WAV archives."""

from sqlalchemy import select

from app.raw_archive.policy import ArchiveBlocked
from app.services.signal_feature_contract import canonical_sha256

FORMAT = "direct-scientific-history-v1"
MAX_RESULTS = 4096


def validate_history(history, revision):
    from app.raw_archive.feature_backup import validate_v2_row

    if (
        not isinstance(history, dict)
        or set(history)
        != {"format", "segment_id", "revision", "manifest_sha256", "results", "sha256"}
        or history["format"] != FORMAT
    ):
        raise ArchiveBlocked("Unknown Direct scientific history contract")
    if (history["segment_id"], history["revision"], history["manifest_sha256"]) != (
        revision["segment_id"],
        revision["revision"],
        revision["manifest_sha256"],
    ):
        raise ArchiveBlocked("Scientific history revision mismatch")
    rows = history["results"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_RESULTS:
        raise ArchiveBlocked("Scientific history result inventory is missing or too large")
    if history["sha256"] != canonical_sha256({k: history[k] for k in history if k != "sha256"}):
        raise ArchiveBlocked("Scientific history digest differs")
    manifest = revision["manifest"]
    runs = {
        (run["key"], run["sha256"], run["first_frame"], run["frame_count"])
        for run in manifest["runs"]
    }
    labels = manifest["config"]["channel_labels"]
    if (
        len(runs) != len(manifest["runs"])
        or len({run[0] for run in runs}) != len(runs)
        or not labels
        or len(set(labels)) != len(labels)
    ):
        raise ArchiveBlocked("Scientific source runs or channels are ambiguous")
    covered, ids, inputs_seen = set(), set(), set()
    for row in rows:
        validate_v2_row("direct", "direct_feature_result", row)
        inputs = row["inputs"]
        run = (
            inputs["wav_key"],
            inputs["wav_sha256"],
            inputs["first_frame"],
            inputs["frame_count"],
        )
        channel = inputs["channel_index"]
        if (
            row["verified_at"] is None
            or row["id"] in ids
            or row["input_sha256"] in inputs_seen
            or (row["segment_id"], row["revision"], row["manifest_sha256"])
            != (history["segment_id"], history["revision"], history["manifest_sha256"])
            or (inputs["device_id"], inputs["session_id"])
            != (manifest["device_id"], manifest["session_id"])
            or run not in runs
            or type(channel) is not int
            or not 0 <= channel < len(labels)
            or inputs["channel_label"] != labels[channel]
        ):
            raise ArchiveBlocked("Unverified or foreign scientific result")
        ids.add(row["id"])
        inputs_seen.add(row["input_sha256"])
        covered.add((run, channel))
    if covered != {(run, channel) for run in runs for channel in range(len(labels))}:
        raise ArchiveBlocked("Scientific history does not cover every run and channel")
    return history["sha256"]


def read_history(db, revision):
    from app.direct.models import FeatureResult
    from app.raw_archive.feature_backup import FEATURE_V2_COLUMNS

    rows = (
        db.execute(
            select(FeatureResult)
            .where(
                FeatureResult.segment_id == revision["segment_id"],
                FeatureResult.revision == revision["revision"],
            )
            .order_by(FeatureResult.input_sha256)
            .limit(MAX_RESULTS + 1)
        )
        .scalars()
        .all()
    )
    values = [
        {name: getattr(row, name) for name in FEATURE_V2_COLUMNS["direct_feature_result"]}
        for row in rows
    ]
    body = {
        "format": FORMAT,
        "segment_id": revision["segment_id"],
        "revision": revision["revision"],
        "manifest_sha256": revision["manifest_sha256"],
        "results": values,
    }
    history = {**body, "sha256": canonical_sha256(body)}
    validate_history(history, revision)
    return history


def scientific_descriptor(manifest, history, reference):
    """Create the pre-snapshot descriptor for exactly one published revision."""
    from .feature_backup import FORMAT_DIRECT_REVISION, check_manifest

    check_manifest(manifest)
    if manifest["format"] != FORMAT_DIRECT_REVISION:
        raise ArchiveBlocked("Scientific descriptor requires a revision-scoped package")
    if manifest["scientific_coverage"] != {
        key: history[key] for key in ("segment_id", "revision", "manifest_sha256")
    }:
        raise ArchiveBlocked("Scientific package coverage differs from history")
    if not isinstance(reference, dict) or set(reference) != {"key", "sha256", "size"}:
        raise ArchiveBlocked("Exact published feature manifest reference required")
    parts = [
        {
            "file": entry["file"],
            "key": entry.get("remote_key"),
            "size": entry["bytes"],
            "sha256": entry["sha256"],
        }
        for entry in manifest["files"]
    ]
    if any(not isinstance(part["key"], str) for part in parts):
        raise ArchiveBlocked("Published feature part references are required")
    return {
        "format": "direct-feature-proof-v2",
        "source": "direct",
        "revision": f"{history['segment_id']}:{history['revision']}",
        "manifest_sha256": history["manifest_sha256"],
        "history_sha256": history["sha256"],
        "manifest": dict(reference),
        "parts": parts,
    }


def verify_scientific_snapshot(
    descriptor, recording, destination, snapshot, now, folder, scientific_source
):
    """Build post-snapshot proof only after actual fresh scoped readback succeeds."""
    from .deletion import verify_feature_v2

    binding = {
        "provider_id": snapshot.get("provider_id"),
        "box_id": snapshot.get("box_id", snapshot.get("storage_box_id")),
        "name": snapshot.get("name"),
        "created_at": snapshot.get("created_at"),
    }
    proof = {
        **descriptor,
        "provider_snapshot": binding,
        "checked_at": now.isoformat(),
        "restored_history_sha256": descriptor["history_sha256"],
    }
    verify_feature_v2(proof, recording, destination, snapshot, now, folder, scientific_source)
    return proof
