"""COPY-only historical Direct revisions; never infer old assignments today."""

import re

from .policy import ArchiveBlocked

COPY_SCOPE = "historical-direct-revision-copy-v1"


def need(value, reason):
    if not value:
        raise ArchiveBlocked(reason)


def complete_history(manifest, segment_id, revision):
    # Full existing scientific allowlist/config/checksum validation is still used.
    from .catalog import canonical
    from .wav_catalog import SIGNAL_FIELDS, validate_row

    validate_row(
        "direct",
        "direct_revision",
        dict(
            segment_id=segment_id,
            revision=revision.revision,
            manifest=manifest,
            manifest_sha256=revision.manifest_sha256,
            verified_at=revision.verified_at,
            raw_deleted_at=revision.raw_deleted_at,
        ),
    )
    need(
        canonical(manifest) == revision.manifest_sha256,
        "Direct historical manifest checksum mismatch",
    )
    need(
        manifest["status"] == "complete"
        and manifest["missing_frame_ranges"] == []
        and manifest["runs"]
        and len(manifest["runs"]) <= 1024,
        "Only fully complete historical Direct revisions can be copied",
    )
    config = manifest["config"]
    need(
        isinstance(manifest["source_mode"], str)
        and manifest["source_mode"] in {"DIRECT", "DUAL"}
        and manifest["analytics_scope"]
        == ("comparison" if manifest["source_mode"] == "DUAL" else "direct"),
        "Historical acquisition mode is unverified",
    )
    need(
        (config["device_id"], config["session_id"])
        == (manifest["device_id"], manifest["session_id"]),
        "Historical config identity mismatch",
    )
    cursor, keys = manifest["first_frame"], set()
    need(
        type(cursor) is int
        and type(manifest["end_frame"]) is int
        and 0 <= cursor < manifest["end_frame"],
        "Invalid historical frame interval",
    )
    for run in manifest["runs"]:
        need(
            type(run["first_frame"]) is int
            and run["first_frame"] == cursor
            and type(run["frame_count"]) is int
            and run["frame_count"] > 0,
            "Historical Direct sample coverage is incomplete",
        )
        started = config["session_start_us"] + cursor * 1000000 // config["sample_rate"]
        need(run["started_at_us"] == started, "Historical sample timestamp differs")
        need(
            isinstance(run["key"], str)
            and run["key"].endswith(".wav")
            and run["key"] not in keys
            and isinstance(run["sha256"], str)
            and re.fullmatch("[a-f0-9]{64}", run["sha256"]),
            "Invalid historical WAV identity",
        )
        features = run["features"]
        need(
            isinstance(features, list) and len(features) == config["channels"],
            "Historical channel features are incomplete",
        )
        labels = []
        for feature in features:
            need(
                set(SIGNAL_FIELDS) <= set(feature)
                and type(feature.get("sample_count")) is int
                and feature["sample_count"] == run["frame_count"]
                and isinstance(feature.get("unit"), str)
                and 0 < len(feature["unit"]) <= 32
                and isinstance(feature.get("extractor_version"), str)
                and 0 < len(feature["extractor_version"]) <= 50
                and isinstance(feature.get("parameter_hash"), str)
                and re.fullmatch("[a-f0-9]{64}", feature["parameter_hash"]),
                "Historical calculation version is unverified",
            )
            labels.append(feature.get("channel"))
        need(labels == config["channel_labels"], "Historical channel labels differ")
        keys.add(run["key"])
        cursor += run["frame_count"]
    need(cursor == manifest["end_frame"], "Historical final samples are missing")
    return manifest
