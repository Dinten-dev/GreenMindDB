"""Device credentials are distinct from existing Gateway API keys."""

import hashlib
import re
import secrets
from hmac import compare_digest
from uuid import UUID

from app.direct.models import Device
from app.direct.protocol import DirectError

TOKEN_RE = re.compile(r"^gmd_([0-9a-f]{32})_([A-Za-z0-9_-]{43})$")


def issue_token(device_id: str):
    token = f"gmd_{UUID(device_id).hex}_{secrets.token_urlsafe(32)}"
    return token, hashlib.sha256(token.encode()).hexdigest()


def authenticate(db, token: str, *, lock=False):
    match = TOKEN_RE.fullmatch(token)
    if not match:
        raise DirectError(401, "device_auth_failed")
    query = db.query(Device).filter(Device.id == str(UUID(hex=match[1])))
    if lock:
        query = query.with_for_update()
    device = query.first()
    actual = hashlib.sha256(token.encode()).hexdigest()
    if not device or not compare_digest(actual, device.key_hash):
        raise DirectError(401, "device_auth_failed")
    if not device.active:
        raise DirectError(403, "device_disabled")
    return device
