"""Additive recording completeness. Never changes archival/retention metadata."""

import math


def gateway_completeness(file):
    rate = file.sample_rate
    duration = file.duration_seconds
    if not rate or not math.isfinite(duration) or duration < 0:
        return {"status": "unknown", "basis": "unavailable"}
    received = round(duration * rate)
    offset = file.started_at.timestamp() % 600
    expected_seconds = 600 - offset
    expected = round(expected_seconds * rate)
    ratio = received / expected if expected else None
    # Old files do not carry a close reason. A short file could reflect an
    # intentional stop or a transport loss; neither is certified as complete.
    status = "complete"
    if received < expected - 1:
        status = "short"
    elif received > expected + rate:
        status = "unknown"
    elif offset >= 1:
        status = "partial_start"
    return {
        "received_samples": received,
        "expected_samples": expected,
        "coverage_ratio": min(1.0, ratio) if ratio is not None else None,
        "missing_seconds": max(0, (expected - received) / rate),
        "received_seconds": duration,
        "expected_seconds": expected_seconds,
        "nominal_seconds": 600,
        "status": status,
        "basis": "nominal_bucket",
        "initial_partial": offset >= 1,
        "close_reason": "unknown",
    }


def direct_completeness(manifest, sealed):
    rate = manifest["config"]["sample_rate"]
    expected = manifest["end_frame"] - manifest["first_frame"]
    received = sum(run["frame_count"] for run in manifest["runs"])
    missing = max(0, expected - received)
    initial = bool(manifest.get("initial_partial"))
    status = (
        "collecting"
        if not sealed
        else ("partial" if missing else "partial_start" if initial else "complete")
    )
    return {
        "received_samples": received,
        "expected_samples": expected,
        "coverage_ratio": min(1, received / expected) if expected > 0 else None,
        "missing_seconds": missing / rate,
        "received_seconds": received / rate,
        "expected_seconds": expected / rate,
        "nominal_seconds": 600,
        "status": status,
        "basis": "verified_manifest",
        "initial_partial": initial,
        "missing_frame_ranges": manifest.get("missing_frame_ranges", []),
    }
