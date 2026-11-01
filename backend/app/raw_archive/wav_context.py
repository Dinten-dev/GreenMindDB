"""Versioned COPY-only observations; never infer an acquisition assignment."""

from .policy import ArchiveBlocked

COPY_SCOPE = "observed-wav-context-copy-v1"
GATEWAY_COPY_SCOPE_V2 = "observed-gateway-wav-context-copy-v2"
DIRECT_COPY_SCOPE_V2 = "observed-direct-wav-context-copy-v2"
DIRECT_VISUAL_FIELDS = frozenset(
    {"segment_id", "source_revision", "seconds", "source", "updated_at", "error", "retry_at"}
)
RECEIPT_FORMAT = "copy-only-wav-context-v1"
UNKNOWN = {
    "status": "unknown",
    "quarantined": True,
    "reason": "capture_assignment_not_proven_by_archive_inputs",
}
FIELDS = {
    "gateway": {"wav", "feature", "feature_versions"},
    "direct": {"segment", "revision", "session"},
}
ASSIGNMENT = {
    "gateway": {"sensor_id", "gateway_id", "zone_id", "organization_id"},
    "direct": {
        "device_id",
        "organization_id",
        "zone_id",
        "legacy_sensor_id",
        "mode",
        "hardware_id",
    },
}


def visual_parent(metadata):
    """Only an actual allowlisted row can establish the visual-wave FK parent."""
    from uuid import UUID

    from .wav_catalog import validate_row

    row = metadata["visual_wav"]
    if row is None:
        return
    validate_row("gateway", "visual_wav", row)
    try:
        same_id = UUID(str(row["wav_id"])) == UUID(str(metadata["wav"]["id"]))
    except (ValueError, AttributeError, TypeError):
        raise ArchiveBlocked("Observed visual WAV parent identity is invalid") from None
    if not same_id or row["source_sha256"] != metadata["wav"]["content_sha256"]:
        raise ArchiveBlocked("Observed visual WAV parent source identity differs")


def direct_visual_parent(metadata):
    """Require an actual observed marker, never infer it from WAV samples."""
    import math
    from datetime import datetime

    row = metadata["direct_visual_segment"]
    if row is None:
        return
    if (
        not isinstance(row, dict)
        or set(row) != DIRECT_VISUAL_FIELDS
        or row["segment_id"] != metadata["segment"]["id"]
        or type(row["source_revision"]) is not int
        or not 0 <= row["source_revision"] <= metadata["segment"]["revision"]
        or type(row["seconds"]) is not int
        or row["seconds"] not in (1, 60, 600)
        or not isinstance(row["source"], str)
        or row["source"] not in {"wav", "readings", "pending"}
        or row["error"] is not None
        and (not isinstance(row["error"], str) or len(row["error"].encode()) > 4096)
        or type(row["retry_at"]) not in (int, float)
        or not math.isfinite(row["retry_at"])
        or row["retry_at"] < 0
        or not isinstance(row["updated_at"], (str, datetime))
    ):
        raise ArchiveBlocked("Exact bounded observed Direct visual parent required")
    try:
        if isinstance(row["updated_at"], str):
            datetime.fromisoformat(row["updated_at"])
    except ValueError:
        raise ArchiveBlocked("Observed Direct visual timestamp differs") from None


def visual_fields(copy_scope):
    if copy_scope == GATEWAY_COPY_SCOPE_V2:
        return {"visual_wav"}
    if copy_scope == DIRECT_COPY_SCOPE_V2:
        return {"direct_visual_segment"}
    return set()


def observe(metadata, kind, *, copy_scope=COPY_SCOPE):
    """Preserve every scientific value; label only today's routing as observed."""
    if (
        kind not in FIELDS
        or not isinstance(metadata, dict)
        or copy_scope not in {COPY_SCOPE, GATEWAY_COPY_SCOPE_V2, DIRECT_COPY_SCOPE_V2}
        or (copy_scope == GATEWAY_COPY_SCOPE_V2 and kind != "gateway")
        or (copy_scope == DIRECT_COPY_SCOPE_V2 and kind != "direct")
        or set(metadata) != FIELDS[kind] | {"assignment"} | visual_fields(copy_scope)
        or not isinstance(metadata["assignment"], dict)
        or set(metadata["assignment"]) != ASSIGNMENT[kind]
    ):
        raise ArchiveBlocked("Exact scientific observation metadata required")
    if copy_scope == GATEWAY_COPY_SCOPE_V2:
        visual_parent(metadata)
    if copy_scope == DIRECT_COPY_SCOPE_V2:
        direct_visual_parent(metadata)
    return {
        **{key: metadata[key] for key in FIELDS[kind] | visual_fields(copy_scope)},
        "capture_assignment": dict(UNKNOWN),
        "observed_assignment": {**metadata["assignment"], "semantics": "observed_at_archive"},
    }


def require_observed(metadata, kind, candidate, *, copy_scope=COPY_SCOPE):
    """A pinned archive may restore observations, never promote unknown context."""
    if (
        kind not in FIELDS
        or not isinstance(metadata, dict)
        or copy_scope not in {COPY_SCOPE, GATEWAY_COPY_SCOPE_V2, DIRECT_COPY_SCOPE_V2}
        or (copy_scope == GATEWAY_COPY_SCOPE_V2 and kind != "gateway")
        or (copy_scope == DIRECT_COPY_SCOPE_V2 and kind != "direct")
        or set(metadata)
        != FIELDS[kind] | {"capture_assignment", "observed_assignment"} | visual_fields(copy_scope)
        or metadata["capture_assignment"] != UNKNOWN
        or metadata["capture_assignment"].get("quarantined") is not True
        or not isinstance(metadata["observed_assignment"], dict)
        or set(metadata["observed_assignment"]) != ASSIGNMENT[kind] | {"semantics"}
        or metadata["observed_assignment"]["semantics"] != "observed_at_archive"
        or candidate.quarantined is not True
        or candidate.complete is not False
        or candidate.dependencies_complete is not False
        or candidate.projections_verified is not False
        or candidate.features_verified is not True
    ):
        raise ArchiveBlocked("Unknown WAV capture context must remain COPY-only quarantined")
    if copy_scope == GATEWAY_COPY_SCOPE_V2:
        visual_parent(metadata)
    if copy_scope == DIRECT_COPY_SCOPE_V2:
        direct_visual_parent(metadata)


def copy_flags():
    return dict(
        complete=False,
        features_verified=True,
        projections_verified=False,
        dependencies_complete=False,
        quarantined=True,
    )


def receipt_context(copy_scope):
    """The existing read-broker receipt also retains the deletion prohibition."""
    from .direct_history import COPY_SCOPE as HISTORICAL_DIRECT

    if copy_scope not in {
        COPY_SCOPE,
        GATEWAY_COPY_SCOPE_V2,
        DIRECT_COPY_SCOPE_V2,
        HISTORICAL_DIRECT,
    }:
        raise ArchiveBlocked("Unknown scientific WAV COPY contract")
    return {
        "format": RECEIPT_FORMAT,
        "copy_scope": copy_scope,
        "deletion_authorized": False,
    }


def require_deletable_receipt(receipt):
    context = receipt.get("source_identity")
    if isinstance(context, dict) and (
        context.get("format") == RECEIPT_FORMAT or "copy_scope" in context
    ):
        raise ArchiveBlocked("COPY-only WAV context never authorizes source deletion")
