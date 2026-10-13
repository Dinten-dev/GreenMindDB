"""Direct OTA sidecar: device-scoped HTTPS, immutable images, no schema changes.

Existing ingest/worker/readers do not import this module. Proxy headers may be
trusted only from the loopback reverse proxy. Never enable public control APIs.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.direct.auth import authenticate
from app.direct.protocol import DirectError

PREFIX = "/api/v1/direct-ingest/ota"
PROFILE = "biolingo-v22-n16r8-direct-ota-v1"
IDENT = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
HASH = re.compile(r"^[0-9a-f]{64}$")
logger = logging.getLogger(__name__)


class OtaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DIRECT_", hide_input_in_errors=True)
    database_url: SecretStr = SecretStr("")
    ota_root: Path = Path("/var/lib/greenmind/direct-ota")
    require_tls: bool = True


class Report(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: UUID
    command_id: str = Field(pattern=IDENT.pattern)
    profile: Literal["biolingo-v22-n16r8-direct-ota-v1"]
    firmware_version: str = Field(pattern=IDENT.pattern)
    status: Literal["staged", "booted", "failed", "rollback"]
    error: str = Field(default="", max_length=64, pattern=r"^[a-z0-9_]*$")
    trial_boot: bool
    slot: Literal["app0", "app1"]
    spool_bytes: int = Field(ge=0, le=4 * 1024 * 1024)
    acknowledgements: int = Field(ge=0, le=4294967295)


def read_policy(root):
    path = root / "policy.json"
    try:
        if not path.exists():
            return {"devices": {}, "releases": {}}
        if path.is_symlink() or path.stat().st_size > 1024 * 1024:
            raise ValueError()
        policy = json.loads(path.read_text())
        if not isinstance(policy["devices"], dict) or not isinstance(policy["releases"], dict):
            raise ValueError()
        return policy
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise DirectError(503, "ota_policy_invalid") from exc


def command_for(root, device_id, command_id=None, *, historical=False):
    policy = read_policy(root)
    command = policy["devices"].get(device_id)
    if historical:
        saved = policy.get("commands", {}).get(command_id)
        if saved is not None:
            command = saved if saved.get("device_id") == device_id else None
    if command is None:
        return None
    try:
        release = policy["releases"][command["release_id"]]
        for key in ("command_id", "from_version"):
            if not IDENT.fullmatch(command[key]):
                raise ValueError()
        if not IDENT.fullmatch(release["version"]) or not HASH.fullmatch(release["sha256"]):
            raise ValueError()
        if (
            release["profile"] != PROFILE
            or release["file"] != release["sha256"] + ".bin"
            or type(release["size"]) is not int
            or not 1024 <= release["size"] <= 0x300000
        ):
            raise ValueError()
        if not isinstance(command["expires_at"], (int, float)):
            raise ValueError()
        if not historical and not time.time() < command["expires_at"] <= time.time() + 8 * 86400:
            return None
        if command_id is not None and command_id != command["command_id"]:
            return None
        return command, release
    except (KeyError, ValueError, TypeError) as exc:
        raise DirectError(503, "ota_policy_invalid") from exc


def verified_image(root, release):
    path = root / "images" / release["file"]
    try:
        # Catalog and images are mounted read-only; O_NOFOLLOW rejects a symlink.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as source:
            if os.fstat(source.fileno()).st_size != release["size"]:
                raise ValueError()
            data = source.read(release["size"] + 1)
        if len(data) != release["size"] or hashlib.sha256(data).hexdigest() != release["sha256"]:
            raise ValueError()
        if data[0] != 0xE9 or int.from_bytes(data[12:14], "little") != 9:
            raise ValueError()
        return data
    except (OSError, ValueError) as exc:
        raise DirectError(503, "ota_image_integrity") from exc


def create_app(settings=None, engine=None):
    cfg = settings or OtaSettings()
    if engine is None:
        if not cfg.database_url.get_secret_value().startswith("postgresql"):
            raise ValueError("Dedicated Direct PostgreSQL required")
        engine = create_engine(
            cfg.database_url.get_secret_value(),
            pool_size=1,
            max_overflow=0,
            pool_timeout=3,
            pool_pre_ping=True,
            connect_args={"options": "-c statement_timeout=3000 -c lock_timeout=1000"},
        )
    app = FastAPI(title="GreenMind Direct OTA", docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(DirectError)
    async def domain_error(_, exc):
        return JSONResponse({"error": exc.code}, status_code=exc.status)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(_, exc):
        logger.error("direct_ota_database_unavailable type=%s", type(exc).__name__)
        return JSONResponse({"error": "ota_database_unavailable"}, status_code=503)

    def identity(request):
        if cfg.require_tls and request.url.scheme != "https":
            raise DirectError(400, "tls_required")
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer ") or len(authorization) > 128:
            raise DirectError(401, "device_auth_failed")
        # Release the sole DB connection before policy, image, or audit I/O.
        with Session(engine) as db:
            device = authenticate(db, authorization[7:])
            if device.mode != "DIRECT":
                raise DirectError(409, "ota_direct_only")
            return device.id

    @app.get(PREFIX + "/check")
    def check(request: Request, profile: str = "", current_version: str = ""):
        device_id = identity(request)
        if len(profile) > 64 or not IDENT.fullmatch(current_version):
            raise DirectError(422, "ota_client_invalid")
        value = command_for(cfg.ota_root, device_id)
        if value is None or profile != PROFILE:
            return Response(status_code=204)
        command, release = value
        if current_version != command["from_version"] or current_version == release["version"]:
            return Response(status_code=204)
        return {
            "device_id": device_id,
            "profile": PROFILE,
            "command_id": command["command_id"],
            "from_version": command["from_version"],
            "version": release["version"],
            "sha256": release["sha256"],
            "size": release["size"],
        }

    @app.get(PREFIX + "/download/{command_id}")
    def download(request: Request, command_id: str):
        device_id = identity(request)
        if not IDENT.fullmatch(command_id):
            raise DirectError(404, "ota_command_missing")
        value = command_for(cfg.ota_root, device_id, command_id)
        if value is None:
            raise DirectError(404, "ota_command_missing")
        _, release = value
        return Response(
            verified_image(cfg.ota_root, release),
            media_type="application/octet-stream",
            headers={"Cache-Control": "private, no-store"},
        )

    @app.post(PREFIX + "/report")
    async def report(request: Request):
        # Bound the body before parsing; use sync DB work off the async loop.
        from starlette.concurrency import run_in_threadpool

        device_id = await run_in_threadpool(identity, request)
        body = bytearray()
        try:
            async with asyncio.timeout(10):
                async for block in request.stream():
                    body.extend(block)
                    if len(body) > 2048:
                        raise DirectError(413, "ota_report_limit")
        except TimeoutError as exc:
            raise DirectError(408, "ota_report_timeout") from exc
        try:
            value = Report.model_validate_json(body)
        except ValueError as exc:
            raise DirectError(422, "ota_report_invalid") from exc
        if str(value.device_id) != device_id:
            raise DirectError(403, "device_identity_mismatch")
        # Late boot results remain reportable after assignment expiry/clear/change.
        # They never authorize downloads or another firmware installation.
        command = command_for(cfg.ota_root, device_id, value.command_id, historical=True)
        if command is None:
            raise DirectError(404, "ota_command_missing")
        assignment, release = command
        expected = release["version"] if value.status == "booted" else assignment["from_version"]
        if (
            value.firmware_version != expected
            or value.status == "booted"
            and value.acknowledgements < 10
            or value.status == "staged"
            and value.spool_bytes != 0
        ):
            raise DirectError(409, "ota_report_inconsistent")
        record = {"at": time.time(), **value.model_dump(mode="json")}

        def save():
            audit = cfg.ota_root / "reports"
            audit.mkdir(mode=0o700, exist_ok=True)
            # Single uvicorn worker; O_APPEND keeps bounded lines atomic.
            fd = os.open(
                audit / (device_id + ".jsonl"),
                os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
                0o600,
            )
            with os.fdopen(fd, "a") as output:
                output.write(json.dumps(record, separators=(",", ":")) + "\n")
                output.flush()
                os.fsync(output.fileno())

        await run_in_threadpool(save)
        return {"device_id": device_id, "command_id": value.command_id, "status": value.status}

    return app


def service():
    return create_app()
