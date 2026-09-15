"""Hotspot enrollment; dashboard identity is checked by the existing auth API.

No Legacy tables are written. Device-generated credentials make a lost pairing
response retryable without retaining a plaintext credential on the server.
"""

import hashlib
import time
from collections import OrderedDict, deque
from datetime import UTC, datetime
from hmac import compare_digest
from uuid import UUID

import httpx
from fastapi import Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from app.direct.auth import TOKEN_RE
from app.direct.database import transaction
from app.direct.models import Chunk, Device, Enrollment, Pairing
from app.direct.protocol import DirectError

PREFIX = "/api/v1/direct-ingest"


class PairRequest(BaseModel):
    zone_id: UUID


class RegisterRequest(BaseModel):
    code: str = Field(pattern=r"^(?:[A-Za-z0-9]{6}|[A-Za-z0-9]{8})$")
    device_id: UUID
    token: str = Field(min_length=80, max_length=80)
    hardware_id: str = Field(pattern=r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$")


async def dashboard_identity(request, cfg, zone_id=None):
    """Forward only the existing session credential, to a fixed internal URL."""
    headers = {}
    if request.headers.get("authorization"):
        headers["Authorization"] = request.headers["authorization"]
    elif request.cookies.get("access_token"):
        headers["Cookie"] = "access_token=" + request.cookies["access_token"]
    else:
        raise DirectError(401, "dashboard_login_required")
    try:
        async with httpx.AsyncClient(timeout=5, follow_redirects=False, trust_env=False) as client:
            result = await client.get(cfg.dashboard_api_url + "/auth/me", headers=headers)
            if result.status_code != 200:
                raise DirectError(401, "dashboard_login_required")
            user = result.json()
            if not user.get("organization_id") or not user.get("is_active"):
                raise DirectError(403, "organization_required")
            if zone_id is not None:
                if user.get("role") not in ("owner", "admin"):
                    raise DirectError(403, "manager_required")
                zone = await client.get(
                    cfg.dashboard_api_url + "/zones/" + str(zone_id), headers=headers
                )
                if zone.status_code != 200:
                    raise DirectError(404, "zone_not_found")
            return user
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise DirectError(503, "dashboard_unavailable") from exc


async def small_json(request, model):
    body = bytearray()
    async for part in request.stream():
        body.extend(part)
        if len(body) > 1024:
            raise DirectError(413, "request_limit")
    try:
        return model.model_validate_json(body)
    except ValidationError as exc:
        raise DirectError(422, "invalid_pairing_request") from exc


def install_pairing_routes(app):
    cfg = app.state.settings
    attempts = OrderedDict()
    global_attempts = deque()

    def enabled(request):
        if not cfg.ingest_enabled or not cfg.dashboard_api_url or not cfg.dashboard_origin:
            raise DirectError(503, "pairing_disabled")
        if cfg.require_tls and request.url.scheme != "https":
            raise DirectError(400, "tls_required")

    def rate_limit(request):
        # Single event loop; one bounded table shared by both pairing endpoints.
        now = time.monotonic()
        while global_attempts and global_attempts[0] < now - 60:
            global_attempts.popleft()
        key = request.client.host if request.client else "unknown"
        recent = attempts.setdefault(key, deque())
        attempts.move_to_end(key)
        while recent and recent[0] < now - 60:
            recent.popleft()
        if len(recent) >= 10 or len(global_attempts) >= 120:
            raise DirectError(429, "pairing_rate_limit")
        recent.append(now)
        global_attempts.append(now)
        while len(attempts) > 4096:
            attempts.popitem(last=False)

    @app.get(PREFIX + "/setup")
    async def setup(request: Request):
        enabled(request)
        return {"hotspot_pairing": True}

    @app.post(PREFIX + "/pairing-code", status_code=201)
    async def pairing_code(request: Request):
        import secrets

        enabled(request)
        rate_limit(request)
        # Browser cookie mutations require the exact dashboard origin.
        if request.headers.get("origin") != cfg.dashboard_origin:
            raise DirectError(403, "dashboard_origin_required")
        data = await small_json(request, PairRequest)
        user = await dashboard_identity(request, cfg, data.zone_id)
        code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
        expires = time.time() + 600

        def save_code():
            with transaction(app.state.engine) as db:
                db.query(Pairing).filter(Pairing.expires_at < time.time()).delete()
                db.add(
                    Pairing(
                        code_hash=hashlib.sha256(code.encode()).hexdigest(),
                        organization_id=user["organization_id"],
                        zone_id=str(data.zone_id),
                        expires_at=expires,
                    )
                )

        await run_in_threadpool(save_code)
        return {
            "code": code,
            "expires_at": datetime.fromtimestamp(expires, UTC).isoformat(),
            "zone_id": str(data.zone_id),
        }

    @app.post(PREFIX + "/register", status_code=201)
    async def register(request: Request):
        enabled(request)
        rate_limit(request)
        data = await small_json(request, RegisterRequest)
        match = TOKEN_RE.fullmatch(data.token)
        if not match or UUID(hex=match[1]) != data.device_id:
            raise DirectError(422, "invalid_device_credential")
        digest = hashlib.sha256(data.token.encode()).hexdigest()
        code_hash = hashlib.sha256(data.code.upper().encode()).hexdigest()
        device_id, hardware_id = str(data.device_id), data.hardware_id.lower()

        def register_device():
            try:
                with transaction(app.state.engine) as db:
                    code = (
                        db.query(Pairing).filter_by(code_hash=code_hash).with_for_update().first()
                    )
                    if not code or code.expires_at < time.time():
                        raise DirectError(400, "invalid_or_expired_code")
                    if code.device_id:
                        existing = db.get(Device, code.device_id)
                        enrollment = db.get(Enrollment, hardware_id)
                        if (
                            code.device_id != device_id
                            or not existing
                            or not existing.active
                            or not compare_digest(existing.key_hash, digest)
                            or not enrollment
                            or enrollment.device_id != device_id
                        ):
                            raise DirectError(409, "pairing_code_used")
                    else:
                        existing = db.get(Device, device_id)
                        enrollment = db.get(Enrollment, hardware_id)
                        if existing or enrollment:
                            if (
                                not existing
                                or not enrollment
                                or enrollment.device_id != device_id
                                or not existing.active
                                or not compare_digest(existing.key_hash, digest)
                                or existing.organization_id != code.organization_id
                                or existing.zone_id != code.zone_id
                            ):
                                raise DirectError(409, "device_already_registered")
                            code.device_id = device_id
                            return {"status": "paired", "device_id": device_id}
                        db.add(
                            Device(
                                id=device_id,
                                organization_id=code.organization_id,
                                zone_id=code.zone_id,
                                mode="DIRECT",
                                key_hash=digest,
                                active=True,
                                spool_bytes=0,
                            )
                        )
                        db.flush()
                        db.add(Enrollment(hardware_id=hardware_id, device_id=device_id))
                        code.device_id = device_id
            except IntegrityError as exc:
                raise DirectError(409, "device_already_registered") from exc
            return {"status": "paired", "device_id": device_id}

        return await run_in_threadpool(register_device)

    @app.get(PREFIX + "/devices")
    async def devices(request: Request):
        enabled(request)
        user = await dashboard_identity(request, cfg)

        def list_devices():
            with transaction(app.state.engine) as db:
                latest = (
                    db.query(Chunk.device_id, func.max(Chunk.received_at).label("seen"))
                    .group_by(Chunk.device_id)
                    .subquery()
                )
                rows = (
                    db.query(Device, Enrollment.hardware_id, latest.c.seen)
                    .outerjoin(Enrollment, Enrollment.device_id == Device.id)
                    .outerjoin(latest, latest.c.device_id == Device.id)
                    .filter(Device.organization_id == user["organization_id"])
                    .order_by(Device.id)
                    .limit(200)
                    .all()
                )
                return [
                    {
                        "id": d.id,
                        "zone_id": d.zone_id,
                        "hardware_id": mac,
                        "status": "disabled"
                        if not d.active
                        else "online"
                        if seen and seen > time.time() - 60
                        else "offline",
                        "last_seen": datetime.fromtimestamp(seen, UTC).isoformat()
                        if seen
                        else None,
                        "spool_bytes": d.spool_bytes,
                    }
                    for d, mac, seen in rows
                ]

        return await run_in_threadpool(list_devices)
