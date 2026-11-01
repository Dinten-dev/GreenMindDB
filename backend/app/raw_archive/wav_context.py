"""Versioned COPY-only observations; never infer an acquisition assignment."""

from .policy import ArchiveBlocked

COPY_SCOPE = "observed-wav-context-copy-v1"
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


def observe(metadata, kind):
    """Preserve every scientific value; label only today's routing as observed."""
    if (
        kind not in FIELDS
        or not isinstance(metadata, dict)
        or set(metadata) != FIELDS[kind] | {"assignment"}
        or not isinstance(metadata["assignment"], dict)
        or set(metadata["assignment"]) != ASSIGNMENT[kind]
    ):
        raise ArchiveBlocked("Exact scientific observation metadata required")
    return {
        **{key: metadata[key] for key in FIELDS[kind]},
        "capture_assignment": dict(UNKNOWN),
        "observed_assignment": {**metadata["assignment"], "semantics": "observed_at_archive"},
    }


def require_observed(metadata, kind, candidate):
    """A pinned archive may restore observations, never promote unknown context."""
    if (
        kind not in FIELDS
        or not isinstance(metadata, dict)
        or set(metadata) != FIELDS[kind] | {"capture_assignment", "observed_assignment"}
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

    if copy_scope not in {COPY_SCOPE, HISTORICAL_DIRECT}:
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
