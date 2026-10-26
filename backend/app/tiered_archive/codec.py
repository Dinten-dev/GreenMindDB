"""Lossless, deterministic database values and bounded content identities."""

import base64
import hashlib
import json
import math
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from app.raw_archive.policy import ArchiveBlocked

MAX_ROW_BYTES = 1024 * 1024


def epoch_us(value):
    if value.tzinfo is None:
        raise ArchiveBlocked("Timezone-aware measurement timestamp required")
    delta = value.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def pack(value):
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"$type": "bytes", "value": base64.b64encode(value).decode("ascii")}
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ArchiveBlocked("Naive archive timestamp")
        return {"$type": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"$type": "date", "value": value.isoformat()}
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ArchiveBlocked("Nonfinite decimal")
        return {"$type": "decimal", "value": str(value)}
    if isinstance(value, uuid.UUID):
        return {"$type": "uuid", "value": str(value)}
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ArchiveBlocked("Archive object keys must be strings")
        # Tag every mapping so user JSON can never impersonate a codec tag.
        return {"$type": "mapping", "value": {key: pack(item) for key, item in value.items()}}
    if isinstance(value, (list, tuple)):
        return [pack(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ArchiveBlocked("Nonfinite archive value")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ArchiveBlocked("Unsupported database archive type")


def unpack(value):
    if isinstance(value, list):
        return [unpack(item) for item in value]
    if not isinstance(value, dict):
        return value
    if set(value) != {"$type", "value"}:
        raise ArchiveBlocked("Malformed archive value")
    kind, body = value["$type"], value["value"]
    if kind == "mapping":
        return {key: unpack(item) for key, item in body.items()}
    if kind == "bytes":
        return base64.b64decode(body, validate=True)
    if kind == "datetime":
        result = datetime.fromisoformat(body)
        if result.tzinfo is None:
            raise ArchiveBlocked("Naive archived timestamp")
        return result
    if kind == "date":
        return date.fromisoformat(body)
    if kind == "decimal":
        return Decimal(body)
    if kind == "uuid":
        return uuid.UUID(body)
    raise ArchiveBlocked("Unknown archive value version")


def encode(value):
    raw = json.dumps(pack(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(raw) > MAX_ROW_BYTES:
        raise ArchiveBlocked("Archive row exceeds 1 MiB")
    return raw


def decode(raw):
    if len(raw) > MAX_ROW_BYTES:
        raise ArchiveBlocked("Archived row exceeds 1 MiB")
    return unpack(json.loads(raw))


def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()
