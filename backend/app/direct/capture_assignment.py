"""Server-only first-accept assignment; old sessions are never backfilled."""

import hashlib
import json
from uuid import UUID

FIELD = "_server_capture_assignment_v1"
FORMAT = "authenticated-direct-session-assignment.v1"
FIELDS = {
    "format",
    "device_id",
    "session_id",
    "organization_id",
    "zone_id",
    "mode",
    "accepted_at_us",
    "semantics",
    "sha256",
}


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def validate(value, device_id, session_id):
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError("invalid_server_capture_assignment")
    for field in ("device_id", "session_id", "organization_id", "zone_id"):
        if not isinstance(value[field], str) or str(UUID(value[field])) != value[field]:
            raise ValueError("invalid_server_capture_assignment")
    if (
        value["format"] != FORMAT
        or value["semantics"] != "authenticated_at_first_accept"
        or value["device_id"] != device_id
        or value["session_id"] != session_id
        or value["mode"] not in {"DIRECT", "DUAL"}
        or type(value["accepted_at_us"]) is not int
        or not 0 < value["accepted_at_us"] < 2**63
        or value["sha256"] != digest({k: v for k, v in value.items() if k != "sha256"})
    ):
        raise ValueError("invalid_server_capture_assignment")
    return value


def create(device, session_id, now):
    value = dict(
        format=FORMAT,
        device_id=device.id,
        session_id=session_id,
        organization_id=device.organization_id,
        zone_id=device.zone_id,
        mode=device.mode,
        accepted_at_us=int(now * 1_000_000),
        semantics="authenticated_at_first_accept",
    )
    value["sha256"] = digest(value)
    return validate(value, device.id, session_id)


def split(config):
    if not isinstance(config, dict):
        raise ValueError("invalid_server_capture_config")
    plain = {k: v for k, v in config.items() if k != FIELD}
    assignment = config.get(FIELD)
    if FIELD in config:
        validate(assignment, plain["device_id"], plain["session_id"])
    return plain, assignment


def manifest_assignment(manifest):
    has_assignment = "capture_assignment" in manifest
    if has_assignment != ("metadata_version" in manifest):
        raise ValueError("invalid_capture_manifest_version")
    if not has_assignment:
        return None
    if type(manifest["metadata_version"]) is not int or manifest["metadata_version"] != 2:
        raise ValueError("invalid_capture_manifest_version")
    return validate(manifest["capture_assignment"], manifest["device_id"], manifest["session_id"])
