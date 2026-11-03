"""Authenticated proxy to the isolated RAM owner; raw ingest never uses it."""

import asyncio
import hashlib
import json
import threading
import uuid

import httpx
from fastapi import Query, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from app.direct import pairing
from app.direct.auth import authenticate
from app.direct.database import transaction
from app.direct.live_protocol import RAW_PROFILE, CaptureSpec, MarkRequest, PreviewPacket
from app.direct.models import Device, Session
from app.direct.protocol import DirectError

PREFIX = "/api/v1/direct-ingest/devices/{device_id}"


class PrivateLiveJSON(JSONResponse):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.headers["Cache-Control"] = "private, no-store"


async def bounded_json(request, maximum):
    if request.headers.get("content-encoding", "identity") != "identity":
        raise DirectError(415, "live_compression_not_supported")
    body = bytearray()
    try:
        async with asyncio.timeout(1):
            async for part in request.stream():
                if len(body) + len(part) > maximum:
                    raise DirectError(413, "live_request_limit")
                body.extend(part)
    except TimeoutError:
        raise DirectError(408, "live_request_timeout") from None
    try:
        value = json.loads(body or b"{}")
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeError):
        raise DirectError(422, "invalid_live_request") from None


def install_live_routes(app):
    cfg = app.state.settings
    requests = threading.BoundedSemaphore(16)

    def enabled(request):
        if not cfg.ingest_enabled or not cfg.live_enabled or cfg.live_socket is None:
            raise DirectError(503, "live_disabled")
        if cfg.require_tls and request.url.scheme != "https":
            raise DirectError(400, "tls_required")

    def scope(request, organization):
        request.state.live_namespace = hashlib.sha256(
            (cfg.environment + "\0" + cfg.dashboard_origin + "\0" + str(organization)).encode()
        ).hexdigest()

    async def dispatch(request, data):
        data = {**data, "namespace": request.state.live_namespace}
        # Fixed private Unix socket, no network target or credential from clients.
        override = getattr(app.state, "live_dispatch", None)
        if override is not None:
            return await override(data)
        try:
            async with httpx.AsyncClient(
                transport=httpx.AsyncHTTPTransport(uds=str(cfg.live_socket)),
                base_url="http://direct-live",
                timeout=0.25,
                trust_env=False,
            ) as client:
                response = await client.post("/dispatch", json=data)
            if response.status_code != 200:
                raise DirectError(
                    response.status_code, response.json().get("error", "live_unavailable")
                )
            return response.json()
        except (httpx.HTTPError, ValueError):
            raise DirectError(503, "live_unavailable") from None

    async def viewer(request, device_id, *, mutate=False):
        enabled(request)
        if mutate and request.headers.get("origin") != cfg.dashboard_origin:
            raise DirectError(403, "dashboard_origin_required")
        if not cfg.dashboard_api_url or not cfg.dashboard_origin:
            raise DirectError(503, "live_disabled")
        if not requests.acquire(blocking=False):
            raise DirectError(429, "live_request_limit")
        try:
            user = await pairing.dashboard_identity(request, cfg)
        finally:
            requests.release()

        def scoped_device():
            with transaction(app.state.engine) as db:
                device = db.get(Device, str(device_id))
                if (
                    not device
                    or device.zone_id not in user.get("allowed_zone_ids", [])
                    or (
                        not user.get("platform_admin")
                        and device.organization_id != str(user.get("organization_id"))
                    )
                ):
                    raise DirectError(404, "sensor_not_found")
                if not device.active:
                    raise DirectError(403, "device_disabled")
                return device.organization_id

        scope(request, await run_in_threadpool(scoped_device))
        credential = request.headers.get("authorization") or request.cookies.get("access_token", "")
        if not user.get("id") or not credential:
            raise DirectError(401, "dashboard_login_required")
        return hashlib.sha256((str(user["id"]) + "\0" + credential).encode()).hexdigest()

    async def device(request, device_id, spec=None):
        enabled(request)
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer ") or len(auth) > 128:
            raise DirectError(401, "device_auth_failed")

        def check():
            with transaction(app.state.engine) as db:
                row = authenticate(db, auth[7:])
                if row.id != str(device_id):
                    raise DirectError(403, "device_identity_mismatch")
                if spec is not None:
                    session = db.get(Session, (row.id, str(spec.session_id)))
                    if session is None:
                        raise DirectError(409, "live_raw_session_required")
                    config = session.config
                    expected_profile = (
                        "ad8232-raw400-v1" if spec.preview_profile == RAW_PROFILE else None
                    )
                    if (
                        config["sample_rate"] != spec.source_rate
                        or config["session_start_us"] != spec.session_start_us
                        or config["sample_bits"] != 16
                        or config["channels"] != 1
                        or config["calibration_version"] != "unsigned-mv-linear-int16-v1"
                        or config.get("signal_profile") != expected_profile
                        or config["firmware_version"] != spec.firmware_version
                    ):
                        raise DirectError(409, "live_raw_configuration_mismatch")
                return row.organization_id

        scope(request, await run_in_threadpool(check))

    @app.put(PREFIX + "/live-leases/{lease_id}", response_class=PrivateLiveJSON)
    async def lease(device_id: uuid.UUID, lease_id: uuid.UUID, request: Request):
        principal = await viewer(request, device_id, mutate=True)
        if await bounded_json(request, 256):
            raise DirectError(422, "invalid_live_request")
        return await dispatch(
            request,
            dict(op="lease", device=str(device_id), lease=str(lease_id), principal=principal),
        )

    @app.delete(PREFIX + "/live-leases/{lease_id}", response_class=PrivateLiveJSON)
    async def release(device_id: uuid.UUID, lease_id: uuid.UUID, request: Request):
        principal = await viewer(request, device_id, mutate=True)
        if await bounded_json(request, 256):
            raise DirectError(422, "invalid_live_request")
        return await dispatch(
            request,
            dict(op="release", device=str(device_id), lease=str(lease_id), principal=principal),
        )

    @app.get(PREFIX + "/control", response_class=PrivateLiveJSON)
    async def control(device_id: uuid.UUID, request: Request):
        enabled(request)
        try:
            spec = CaptureSpec.model_validate(dict(request.query_params))
        except ValidationError:
            # Query numbers arrive as text; convert only the explicitly declared clock.
            try:
                data = dict(request.query_params)
                data["session_start_us"] = int(data["session_start_us"])
                data["source_rate"] = int(data["source_rate"])
                data["raw_rate"] = int(data["raw_rate"])
                spec = CaptureSpec.model_validate(data)
            except (ValueError, KeyError, ValidationError):
                raise DirectError(422, "invalid_live_capability") from None
        await device(request, device_id, spec)
        return await dispatch(
            request, dict(op="control", device=str(device_id), spec=spec.model_dump(mode="json"))
        )

    @app.post(PREFIX + "/live-preview", response_class=PrivateLiveJSON)
    async def push(device_id: uuid.UUID, request: Request):
        await device(request, device_id)
        body = await bounded_json(request, 4096)
        try:
            packet = PreviewPacket.model_validate(body)
        except ValidationError:
            raise DirectError(422, "invalid_live_packet") from None
        return await dispatch(
            request, dict(op="push", device=str(device_id), packet=packet.model_dump(mode="json"))
        )

    @app.get(PREFIX + "/live-preview", response_class=PrivateLiveJSON)
    async def preview(
        device_id: uuid.UUID,
        request: Request,
        lease_id: uuid.UUID,
        seconds: int = Query(30, ge=30, le=180),
        after: int = Query(0, ge=0, le=2**53 - 1),
        generation: uuid.UUID | None = None,
    ):
        principal = await viewer(request, device_id)
        return await dispatch(
            request,
            dict(
                op="preview",
                device=str(device_id),
                lease=str(lease_id),
                principal=principal,
                seconds=seconds,
                after=after,
                generation=str(generation) if generation else "",
            ),
        )

    @app.post(PREFIX + "/live-markers/{marker_id}", response_class=PrivateLiveJSON)
    async def marker(
        device_id: uuid.UUID, marker_id: uuid.UUID, request: Request, lease_id: uuid.UUID
    ):
        principal = await viewer(request, device_id, mutate=True)
        try:
            body = MarkRequest.model_validate(await bounded_json(request, 512))
        except ValidationError:
            raise DirectError(422, "invalid_live_marker") from None
        return await dispatch(
            request,
            dict(
                op="mark",
                device=str(device_id),
                lease=str(lease_id),
                principal=principal,
                marker=str(marker_id),
                mark=body.model_dump(mode="json"),
            ),
        )
