"""Separate ASGI app; existing app.main and its routes remain unchanged."""

import asyncio
import hashlib
import logging
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from app.direct.auth import authenticate
from app.direct.config import DirectSettings
from app.direct.database import make_engine, transaction
from app.direct.ingest import accept_chunk
from app.direct.models import Budget, Heartbeat, Revision, Segment
from app.direct.protocol import ChunkMetadata, DirectError
from app.direct.storage import ArtifactStore

logger = logging.getLogger(__name__)
PREFIX = "/api/v1/direct-ingest"


def create_app(settings=None, engine=None, store=None):
    cfg = settings or DirectSettings()
    app = FastAPI(title="GreenMind Direct ingest", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = cfg
    app.state.engine = engine or (make_engine(cfg) if cfg.ingest_enabled else None)
    app.state.store = store or (ArtifactStore(cfg) if cfg.ingest_enabled else None)
    from app.direct.pairing import install_pairing_routes

    install_pairing_routes(app)

    @app.exception_handler(DirectError)
    async def domain_error(_, exc):
        headers = {"Retry-After": "30"} if exc.status in (429, 503) else {}
        return JSONResponse({"error": exc.code}, status_code=exc.status, headers=headers)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(_, exc):
        # Do not log SQL parameters: they may contain payloads or credentials.
        logger.error("direct_database_unavailable type=%s", type(exc).__name__)
        return JSONResponse(
            {"error": "direct_database_unavailable"}, status_code=503, headers={"Retry-After": "30"}
        )

    def credentials(request):
        if not cfg.ingest_enabled:
            raise DirectError(503, "direct_disabled")
        if cfg.require_tls and request.url.scheme != "https":
            raise DirectError(400, "tls_required")
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer ") or len(authorization) > 128:
            raise DirectError(401, "device_auth_failed")
        return authorization[7:]

    @app.get("/health")
    def health():
        if not cfg.ingest_enabled:
            return {"direct_ingest": "disabled"}
        with transaction(app.state.engine) as db:
            db.execute(text("SELECT 1"))
            budget = db.get(Budget, 1)
            if budget is None:
                raise DirectError(503, "direct_schema_not_initialized")
            beat = db.get(Heartbeat, "assembler")
            return {
                "direct_ingest": "healthy",
                "spool_bytes": budget.used_bytes,
                "assembler": beat.status
                if beat and beat.updated_at > time.time() - max(90, cfg.poll_seconds * 3)
                else "unavailable",
            }

    @app.post(PREFIX + "/chunks")
    async def upload(request: Request):
        token = credentials(request)
        if (
            request.headers.get("content-type", "").split(";")[0].strip()
            != "application/octet-stream"
        ):
            raise DirectError(415, "binary_pcm_required")
        if request.headers.get("content-encoding", "identity") != "identity":
            raise DirectError(415, "compression_not_supported")
        metadata = request.headers.get("x-greenmind-metadata", "")
        if len(metadata.encode()) > 2048:
            raise DirectError(413, "metadata_limit")
        try:
            meta = ChunkMetadata.model_validate_json(metadata)
        except (ValidationError, ValueError) as exc:
            raise DirectError(422, "invalid_metadata") from exc
        expected = meta.frame_count * meta.frame_bytes
        if expected > cfg.max_chunk_bytes:
            raise DirectError(413, "payload_limit")

        def preauthenticate():
            with transaction(app.state.engine) as db:
                device = authenticate(db, token)
                if device.id != str(meta.device_id):
                    raise DirectError(403, "device_identity_mismatch")

        await run_in_threadpool(preauthenticate)
        body = bytearray()
        try:
            async with asyncio.timeout(20):
                async for block in request.stream():
                    if len(body) + len(block) > expected:
                        raise DirectError(413, "payload_limit")
                    body.extend(block)
        except TimeoutError as exc:
            raise DirectError(408, "upload_timeout") from exc
        result = await run_in_threadpool(
            accept_chunk, app.state.engine, cfg, token, meta, bytes(body)
        )
        return JSONResponse(result, status_code=200 if result["status"] == "duplicate" else 201)

    @app.get(PREFIX + "/segments")
    def segments(request: Request):
        token = credentials(request)
        with transaction(app.state.engine) as db:
            device = authenticate(db, token)
            rows = (
                db.query(Segment, Revision)
                .join(
                    Revision,
                    (Revision.segment_id == Segment.id)
                    & (Revision.revision == Segment.published_revision),
                )
                .filter(Segment.device_id == device.id)
                .order_by(Segment.bucket.desc())
                .limit(100)
                .all()
            )
            return [
                {
                    "segment_id": seg.id,
                    "sealed": seg.sealed,
                    "raw_available": revision.raw_deleted_at is None,
                    "manifest": revision.manifest,
                    "manifest_sha256": revision.manifest_sha256,
                }
                for seg, revision in rows
            ]

    @app.get(PREFIX + "/segments/{segment_id}/runs/{run_index}")
    def download(segment_id: str, run_index: int, request: Request):
        token = credentials(request)
        with transaction(app.state.engine) as db:
            device = authenticate(db, token)
            segment = db.query(Segment).filter_by(id=segment_id, device_id=device.id).first()
            revision = (
                db.get(Revision, (segment.id, segment.published_revision)) if segment else None
            )
            if revision is None or not 0 <= run_index < len(revision.manifest["runs"]):
                raise DirectError(404, "run_not_found")
            if revision.raw_deleted_at is not None:
                raise DirectError(410, "raw_expired")
            run = revision.manifest["runs"][run_index]
        data = app.state.store.get(run["key"])
        if hashlib.sha256(data).hexdigest() != run["sha256"]:
            raise DirectError(503, "artifact_integrity_failed")
        return Response(
            data, media_type="audio/wav", headers={"Cache-Control": "private, no-store"}
        )

    return app


app = create_app()
