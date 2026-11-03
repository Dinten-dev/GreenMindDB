"""Central gateway API-key generation and authentication.

New keys embed the gateway UUID so authentication requires one database lookup.
Legacy keys need a device identity until controlled digest enrollment. Public
requests never scan the fleet, including on cache misses and malformed keys.
"""

from __future__ import annotations

import re
import secrets
import time
import uuid
from collections import OrderedDict
from hashlib import sha256
from hmac import compare_digest
from threading import Lock

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.auth import get_password_hash, verify_password
from app.database import get_db
from app.models.master import Gateway

GATEWAY_KEY_PREFIX = "gmk"
_NEW_KEY_RE = re.compile(r"^gmk_([0-9a-f]{32})_([A-Za-z0-9_-]{32})$")
_MAX_API_KEY_LENGTH = 128
_DIGEST_PREFIX = "sha256$"
_CACHE_SECONDS = 300
_CACHE_LIMIT = 1024
# Successful checks only. Each hit still reads current activation/hash from SQL.
# Scope entries to their engine; never retain the plaintext credential.
_verified_keys: OrderedDict = OrderedDict()
_cache_lock = Lock()
# Bound synchronization independently of attacker-chosen keys. Requests with
# the same cold credential share one costly legacy check, then reread SQL.
_verification_locks = tuple(Lock() for _ in range(64))
_legacy_lock = Lock()
_legacy_attempts: OrderedDict = OrderedDict()
_legacy_global = [0.0, 0]


def legacy_hash_filter():
    """The only supported old credential encodings; modern digests never enter the scan."""
    return or_(
        Gateway.api_key_hash.like("$2a$%"),
        Gateway.api_key_hash.like("$2b$%"),
        Gateway.api_key_hash.like("$2y$%"),
        Gateway.api_key_hash.like("$bcrypt-sha256$%"),
    )


def _reserve_legacy_check(peer: str):
    now = time.monotonic()
    with _cache_lock:
        if now - _legacy_global[0] >= 60:
            _legacy_global[:] = [now, 0]
        start, count = _legacy_attempts.get(peer, (now, 0))
        if now - start >= 60:
            start, count = now, 0
        if count >= 5 or _legacy_global[1] >= 30:
            raise HTTPException(
                429, "Legacy authentication temporarily limited", headers={"Retry-After": "60"}
            )
        _legacy_attempts[peer] = (start, count + 1)
        _legacy_attempts.move_to_end(peer)
        while len(_legacy_attempts) > 1024:
            _legacy_attempts.popitem(last=False)
        _legacy_global[1] += 1


def clear_gateway_auth_cache() -> None:
    with _cache_lock:
        _verified_keys.clear()
        _legacy_attempts.clear()
        _legacy_global[:] = [0.0, 0]


def _remember_verified(db, api_key, gateway):
    key = (id(db.get_bind()), sha256(api_key.encode()).digest())
    with _cache_lock:
        _verified_keys[key] = (gateway.id, gateway.api_key_hash, time.monotonic() + _CACHE_SECONDS)
        _verified_keys.move_to_end(key)
        while len(_verified_keys) > _CACHE_LIMIT:
            _verified_keys.popitem(last=False)


def _cached_gateway(db, api_key):
    key = (id(db.get_bind()), sha256(api_key.encode()).digest())
    with _cache_lock:
        entry = _verified_keys.get(key)
        if entry is None:
            return None
        if entry[2] <= time.monotonic():
            _verified_keys.pop(key, None)
            return None
        _verified_keys.move_to_end(key)
    gateway = db.query(Gateway).populate_existing().filter(Gateway.id == entry[0]).first()
    if gateway is None or gateway.api_key_hash != entry[1]:
        with _cache_lock:
            _verified_keys.pop(key, None)
        return None
    if not gateway.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Gateway deactivated")
    return gateway


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


def gateway_key_digest(api_key: str) -> str:
    return f"{_DIGEST_PREFIX}{sha256(api_key.encode()).hexdigest()}"


def _verify_gateway_key(gateway: Gateway, api_key: str | None) -> bool:
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


def verify_gateway_api_key(gateway: Gateway, api_key: str | None, peer: str = "internal") -> bool:
    """Protect direct re-provisioning checks as well as authentication dependencies."""
    if not api_key or len(api_key) > _MAX_API_KEY_LENGTH or not gateway.api_key_hash:
        return False
    if gateway.api_key_hash.startswith(_DIGEST_PREFIX):
        return _verify_gateway_key(gateway, api_key)
    # Do not queue unbounded password work behind a busy verifier. The budget
    # counts individual bcrypt operations, including successful ones.
    if not _legacy_lock.acquire(blocking=False):
        raise HTTPException(
            429, "Legacy authentication temporarily limited", headers={"Retry-After": "60"}
        )
    try:
        _reserve_legacy_check(peer)
        return _verify_gateway_key(gateway, api_key)
    finally:
        _legacy_lock.release()


def authenticate_gateway_api_key(
    db: Session,
    api_key: str | None,
    peer: str = "internal",
    *,
    gateway_id: str | uuid.UUID | None = None,
    hardware_id: str | None = None,
) -> Gateway:
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
    if len(api_key.encode()) > _MAX_API_KEY_LENGTH:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    if gateway_id is not None:
        try:
            gateway_id = uuid.UUID(str(gateway_id))
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(401, "Invalid API key") from None
    if hardware_id is not None and not 1 <= len(hardware_id) <= 100:
        raise HTTPException(401, "Invalid API key")

    def checked(gateway):
        if gateway_id is not None and gateway.id != gateway_id:
            raise HTTPException(401, "Invalid API key")
        if hardware_id is not None and gateway.hardware_id != hardware_id:
            raise HTTPException(403, "Gateway identity mismatch")
        return gateway

    match = _NEW_KEY_RE.fullmatch(api_key)
    if api_key.startswith(f"{GATEWAY_KEY_PREFIX}_") and not match:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    cached = _cached_gateway(db, api_key)
    if cached is not None:
        return checked(cached)
    with _verification_locks[sha256(api_key.encode()).digest()[0] % 64]:
        cached = _cached_gateway(db, api_key)
        if cached is not None:
            return checked(cached)
        return checked(_authenticate_uncached(db, api_key, match, peer, gateway_id, hardware_id))


def _authenticate_uncached(db, api_key, match, peer, gateway_id, hardware_id):
    if match:
        gateway_id = uuid.UUID(hex=match.group(1))
        gateway = db.query(Gateway).populate_existing().filter(Gateway.id == gateway_id).first()
        if not gateway or not gateway.api_key_hash:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
        verified = verify_gateway_api_key(gateway, api_key, peer)
        if not verified:
            raise HTTPException(401, "Invalid API key")
        if not gateway.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Gateway deactivated")
        if not gateway.api_key_hash.startswith(_DIGEST_PREFIX):
            _remember_verified(db, api_key, gateway)
        return gateway

    query = db.query(Gateway).populate_existing()
    # An indexed digest can resolve an enrolled key even when a supplied hint is
    # wrong. The caller still checks the hint after proving the credential.
    matches = query.filter(Gateway.api_key_hash == gateway_key_digest(api_key)).limit(2).all()
    if len(matches) == 1:
        gateway = matches[0]
    elif gateway_id is not None:
        gateway = query.filter(Gateway.id == gateway_id).first()
    elif hardware_id is not None:
        gateway = query.filter(Gateway.hardware_id == hardware_id).first()
    else:
        gateway = None
    if not gateway or not verify_gateway_api_key(gateway, api_key, peer):
        raise HTTPException(401, "Invalid API key")
    if not gateway.is_active:
        raise HTTPException(403, "Gateway deactivated")
    if not gateway.api_key_hash.startswith(_DIGEST_PREFIX):
        _remember_verified(db, api_key, gateway)
    return gateway


def get_current_gateway(
    request: Request,
    x_api_key: str | None = Header(None, alias="X-Api-Key"),
    db: Session = Depends(get_db),
    x_gateway_id: str | None = Header(None, alias="X-Gateway-Id"),
) -> Gateway:
    """FastAPI dependency returning the authenticated, active gateway."""
    return authenticate_gateway_api_key(
        db, x_api_key, request.client.host if request.client else "unknown", gateway_id=x_gateway_id
    )
