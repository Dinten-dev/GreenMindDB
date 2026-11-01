"""Frozen v1 scientific quality semantics; do not change after release."""

import math

_REQUIRED_OBSERVATIONS = frozenset({"coverage", "source_flags", "dropped_samples", "continuity"})


def classify_quality_v1(features, continuity, source_quality, coverage_ratio, observations):
    """Known faults win; incomplete observations never imply validity."""
    if not isinstance(continuity, dict) or set(continuity) - {
        "frame_gap_count",
        "sequence_gap_count",
        "source_dropped_samples_delta",
    }:
        raise ValueError("Unknown continuity fields")
    if set(observations) != _REQUIRED_OBSERVATIONS or any(
        type(v) is not bool for v in observations.values()
    ):
        raise ValueError("Explicit observation status is required")
    if coverage_ratio is not None and (
        not math.isfinite(coverage_ratio) or not 0 <= coverage_ratio <= 1
    ):
        raise ValueError("Invalid coverage")
    if observations["coverage"] != (coverage_ratio is not None):
        raise ValueError("Coverage observation mismatch")
    reasons = []
    if coverage_ratio is not None and coverage_ratio < 0.95:
        reasons.append("low_coverage")
    if features["clipping_ratio"] > 0.001:
        reasons.append("clipping")
    if features["flatline_seconds"] > 5.0:
        reasons.append("flatline")
    for name, reason in (
        ("frame_gap_count", "frame_gaps"),
        ("sequence_gap_count", "sequence_gaps"),
        ("source_dropped_samples_delta", "dropped_samples"),
    ):
        value = continuity.get(name)
        if value is not None:
            if type(value) is not int or value < 0:
                raise ValueError("Invalid continuity count")
            if value > 0:
                reasons.append(reason)
    allowed = {"valid", "lead_off", "rail_high", "rail_low", "jump", "recovery"}
    if set(source_quality) != allowed:
        raise ValueError("Incomplete source quality fields")
    for name in ("valid", "lead_off", "rail_high", "rail_low", "jump", "recovery"):
        value = source_quality[name]
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError("Invalid source quality count")
        if name != "valid" and value:
            reasons.append(name)
    if observations["source_flags"] and any(value is None for value in source_quality.values()):
        raise ValueError("Incomplete observed flags")
    if observations["dropped_samples"] and continuity.get("source_dropped_samples_delta") is None:
        raise ValueError("Missing observed dropped-sample count")
    if observations["continuity"] and not any(
        continuity.get(k) is not None for k in ("frame_gap_count", "sequence_gap_count")
    ):
        raise ValueError("Missing observed continuity")
    status = "technical_fault" if reasons else "valid" if all(observations.values()) else "unknown"
    biological = status == "valid" and features["outlier_ratio"] > 0.001
    return {
        "data_quality_status": status,
        "technical_fault_reasons": reasons,
        "biological_candidate": biological if status == "valid" else None,
        "biological_candidate_reasons": ["robust_outliers"] if biological else [],
        "observation_status": dict(observations),
    }
