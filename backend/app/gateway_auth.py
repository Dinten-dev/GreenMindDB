"""Central gateway API-key generation and authentication.

New keys embed the gateway UUID so authentication requires one database lookup.
Legacy unprefixed keys remain valid through an explicitly isolated O(N) fallback
until operators rotate them.
"""

from __future__ import annotations

import re
import secrets
import uuid
from hashlib import sha256
from hmac import compare_digest

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import get_password_hash, verify_password
from app.database import get_db
from app.models.master import Gateway

GATEWAY_KEY_PREFIX = "gmk"
_NEW_KEY_RE = re.compile(r"^gmk_([0-9a-f]{32})_([A-Za-z0-9_-]{32})$")
_MAX_API_KEY_LENGTH = 128
_DIGEST_PREFIX = "sha256$"


def generate_gateway_api_key(gateway_id: uuid.UUID) -> str:
    """Return a high-entropy key carrying a non-secret lookup identifier."""
    # Keep the complete key below bcrypt's 72-byte input boundary.
    return f"{GATEWAY_KEY_PREFIX}_{gateway_id.hex}_{secrets.token_urlsafe(24)}"


def set_gateway_api_key(gateway: Gateway, api_key: str) -> None:
    """Replace a gateway credential; callers commit the surrounding transaction."""
    if _NEW_KEY_RE.fullmatch(api_key):
        # API keys carry 192 bits of random entropy, so a fast one-way digest avoids
        # making every authenticated device request pay bcrypt's password-hardening cost.
        gateway.api_key_hash = f"{_DIGEST_PREFIX}{sha256(api_key.encode()).hexdigest()}"
    else:
        gateway.api_key_hash = get_password_hash(api_key)


def verify_gateway_api_key(gateway: Gateway, api_key: str | None) -> bool:
    """Verify a candidate against one already-resolved gateway."""
    if not api_key or len(api_key) > _MAX_API_KEY_LENGTH or not gateway.api_key_hash:
        return False
    if gateway.api_key_hash.startswith(_DIGEST_PREFIX):
        expected = gateway.api_key_hash.removeprefix(_DIGEST_PREFIX)
        actual = sha256(api_key.encode()).hexdigest()
        return len(expected) == 64 and compare_digest(actual, expected)
    try:
        return verify_password(api_key, gateway.api_key_hash)
    except (TypeError, ValueError):
        return False


def authenticate_gateway_api_key(db: Session, api_key: str | None) -> Gateway:
    """Authenticate a gateway with O(1) lookup for new keys.

    Keys using the new prefix never fall through to the legacy scan when malformed;
    this prevents attacker-controlled prefixed input from reintroducing the bcrypt
    amplification problem.
    """
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Api-Key header",
        )
    if len(api_key) > _MAX_API_KEY_LENGTH:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    match = _NEW_KEY_RE.fullmatch(api_key)
    if match:
        gateway_id = uuid.UUID(hex=match.group(1))
        gateway = db.query(Gateway).filter(Gateway.id == gateway_id).first()
        if not gateway or not verify_gateway_api_key(gateway, api_key):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
        if not gateway.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Gateway deactivated")
        return gateway

    if api_key.startswith(f"{GATEWAY_KEY_PREFIX}_"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    # Compatibility only: remove after all deployed gateways have rotated to gmk_* keys.
    for gateway in db.query(Gateway).filter(Gateway.api_key_hash.isnot(None)).all():
        if verify_gateway_api_key(gateway, api_key):
            if not gateway.is_active:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Gateway deactivated",
                )
            return gateway

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


def get_current_gateway(
    x_api_key: str | None = Header(None, alias="X-Api-Key"),
    db: Session = Depends(get_db),
) -> Gateway:
    """FastAPI dependency returning the authenticated, active gateway."""
    return authenticate_gateway_api_key(db, x_api_key)
