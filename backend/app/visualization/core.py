"""Pure bounded statistics and exact timestamp identities."""

import math
import struct
import zlib
from datetime import UTC, datetime


def epoch_us(stamp):
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    delta = stamp - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def bucket(stamp, seconds):
    return datetime.fromtimestamp(epoch_us(stamp) // (seconds * 1_000_000) * seconds, UTC)


def pack_ids(values):
    ordered = sorted(set(values))
    return zlib.compress(struct.pack(f">{len(ordered)}q", *ordered))


def unpack_ids(data):
    raw = zlib.decompress(bytes(data))
    if len(raw) % 8 or len(raw) > 8_000_000:
        raise ValueError("Invalid identity ledger")
    return set(struct.unpack(f">{len(raw) // 8}q", raw))


def merge_readings(previous, readings):
    """Same timestamp identity is ignored exactly as Legacy ON CONFLICT."""
    state = (
        dict(previous)
        if previous
        else {
            "n": 0,
            "total": 0.0,
            "total2": 0.0,
            "minimum": math.inf,
            "maximum": -math.inf,
            "identities": pack_ids([]),
        }
    )
    identities = unpack_ids(state["identities"])
    for reading in readings:
        identity = epoch_us(reading["timestamp"])
        if identity in identities:
            continue
        value = float(reading["value"])
        if not math.isfinite(value):
            raise ValueError("Nonfinite historical reading")
        identities.add(identity)
        state["n"] += 1
        state["total"] += value
        state["total2"] += value * value
        state["minimum"] = min(state["minimum"], value)
        state["maximum"] = max(state["maximum"], value)
    if state["n"] != len(identities):
        raise ValueError("Identity count does not match aggregate")
    state["identities"] = pack_ids(identities)
    return state
