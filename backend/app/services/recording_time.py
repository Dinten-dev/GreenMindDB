"""Read-only presentation of capture time; never substitutes reception for capture."""

import hashlib
import json
import math
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import text


def utc_instant(microseconds):
    seconds, fraction = divmod(int(microseconds), 1_000_000)
    return datetime.fromtimestamp(seconds, UTC).replace(microsecond=fraction).isoformat()


def utc_filename(prefix, identity, start, end=None, extension="csv"):
    """UTC and microseconds disambiguate repeated local hours and exact export bounds."""

    def stamp(value):
        if value.tzinfo is None:
            raise ValueError("Export bounds require a timezone")
        return value.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")

    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(identity))[:80] or "sensor"
    bounds = stamp(start) + ("_" + stamp(end) if end is not None else "")
    return f"{prefix}_{safe}_{bounds}.{extension}"


def anchor_available(config):
    anchor, rate = config.get("session_start_us"), config.get("sample_rate")
    return (
        type(anchor) is int
        and 1_577_836_800_000_000 <= anchor <= 4_102_444_800_000_000
        and type(rate) is int
        and 0 < rate <= 2000
    )


def verify_manifest(manifest, digest, device_id):
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    if hashlib.sha256(canonical).hexdigest() != digest or manifest["device_id"] != str(device_id):
        raise ValueError("Recording manifest identity mismatch")


def recording_time(manifest, run):
    cfg = manifest["config"]
    known = anchor_available(cfg)
    start = run.get("started_at_us")
    if known:
        expected = cfg["session_start_us"] + run["first_frame"] * 1_000_000 // cfg["sample_rate"]
        if start != expected:
            raise ValueError("Recording capture reference mismatch")
    return {
        "anchor_status": "available" if known else "missing",
        "basis": "session_anchor_and_sample_index" if known else "unknown",
        "physical_accuracy_verified": False,
        "session_id": manifest["session_id"],
        "first_frame": run["first_frame"],
        "sample_count": run["frame_count"],
        "sample_rate": cfg["sample_rate"],
        "anchor_utc": utc_instant(cfg["session_start_us"]) if known else None,
        "recorded_from": utc_instant(start) if isinstance(start, int) else None,
        "recorded_to": utc_instant(start + run["frame_count"] * 1_000_000 // cfg["sample_rate"])
        if isinstance(start, int)
        else None,
        "sha256": run["sha256"],
        "manifest_integrity": "verified",
        "clock_source": (cfg.get("timing") or {}).get("source", "unspecified"),
        "reported_uncertainty_us": (cfg.get("timing") or {}).get("uncertainty_us"),
    }


def direct_context(db, device_id, start, end):
    """Bounded session references, without payload/credential reads or archive mutation."""
    rows = (
        db.execute(
            text("""
        WITH selected AS (
          SELECT session_id,bool_or(NOT sealed) AS ongoing FROM direct_segment
          WHERE device_id=:device AND bucket*600<:end AND (bucket+1)*600>:start
          GROUP BY session_id ORDER BY session_id LIMIT 129
        )
        SELECT c.id,c.config,s.ongoing FROM selected s JOIN direct_session c
          ON c.id=s.session_id AND c.device_id=:device ORDER BY c.id
    """),
            {"device": str(device_id), "start": start, "end": end},
        )
        .mappings()
        .all()
    )
    limited = len(rows) > 128
    rows = rows[:128]
    flags = [anchor_available(r["config"]) for r in rows]
    status = (
        "available"
        if flags and all(flags) and not limited
        else "mixed"
        if any(flags)
        else "missing"
    )
    return {
        "anchor_status": status,
        "basis": "session_anchor_and_sample_index" if status == "available" else "unknown",
        "physical_accuracy_verified": False,
        "ongoing": any(r["ongoing"] for r in rows),
        "sessions_truncated": limited,
        "sessions": [
            {
                "session_id": r["id"],
                "anchor_utc": utc_instant(r["config"]["session_start_us"]) if known else None,
                "sample_rate": r["config"].get("sample_rate"),
                "clock_source": (r["config"].get("timing") or {}).get("source", "unspecified"),
                "reported_uncertainty_us": (r["config"].get("timing") or {}).get("uncertainty_us"),
            }
            for r, known in zip(rows, flags, strict=True)
        ],
    }


def window_time(points, start, end, context=None):
    """Bucket coverage is an overview estimate, never a raw-recording certificate."""
    cursor, covered, gaps, unknown, overlap = start, 0.0, 0, False, False
    first, last = None, None
    for point in sorted(points, key=lambda p: p["timestamp"]):
        try:
            value = datetime.fromisoformat(point["timestamp"])
            if value.tzinfo is None:
                raise ValueError("Timezone required")
            left = max(start, value.astimezone(UTC))
            width = point.get("resolution_seconds")
            if not isinstance(width, (int, float)) or not math.isfinite(width) or width <= 0:
                unknown = True
                continue
            right = min(end, value.astimezone(UTC) + timedelta(seconds=width))
        except (ValueError, TypeError, OverflowError):
            unknown = True
            continue
        if right <= left:
            continue
        first = left if first is None else min(first, left)
        last = right if last is None else max(last, right)
        if left > cursor:
            gaps += 1
        if left < cursor or point.get("signal_source") == "overlap":
            overlap = True
        ratio = point.get("coverage_ratio")
        if ratio is None or not math.isfinite(ratio) or not 0 <= ratio <= 1:
            unknown = True
        else:
            covered += max(0, (right - max(left, cursor)).total_seconds()) * ratio
        cursor = max(cursor, right)
    if cursor < end:
        gaps += 1
    ratio = None if unknown or overlap else min(1, covered / (end - start).total_seconds())
    return {
        **(
            context
            or {"anchor_status": "missing", "basis": "unknown", "physical_accuracy_verified": False}
        ),
        "requested_from": start.isoformat(),
        "requested_to": end.isoformat(),
        "recorded_from": first.isoformat() if first else None,
        "recorded_to": last.isoformat() if last else None,
        "coverage_ratio": ratio,
        "coverage_basis": "display_buckets",
        "gap_count": gaps,
        "coverage_status": "incomplete"
        if gaps or (ratio is not None and ratio < 0.999)
        else "unknown"
        if ratio is None
        else "covered",
        "overlap": overlap,
    }


def annotate_direct(db, device_id, series, start, end):
    if not series:
        return series
    context = direct_context(db, device_id, start.timestamp(), end.timestamp())
    for item in series:
        item["time_info"] = window_time(item["data"], start, end, context)
    return series


def window_bounds(seconds, from_dt=None, to_dt=None):
    end = datetime.now(UTC)
    start = end - timedelta(seconds=seconds)
    if (from_dt is None) != (to_dt is None):
        raise ValueError("Both time bounds are required")
    if from_dt is not None:
        start, end = from_dt, to_dt
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("Time bounds must include a timezone")
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    if not timedelta(0) < end - start <= timedelta(days=3650):
        raise ValueError("Select a positive window of at most ten years")
    return start, end
