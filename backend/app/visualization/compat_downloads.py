"""Old download URLs on the isolated reader; never registers an intake route."""

import hashlib
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.direct.auth import authenticate
from app.direct.models import Revision, Segment
from app.direct.protocol import DirectError
from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.reader import verified_objects
from app.visualization import direct


def device(request, db):
    if request.url.scheme != "https":
        raise HTTPException(400, "tls_required")
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer ") or len(authorization) > 128:
        raise HTTPException(401, "device_auth_failed")
    try:
        return authenticate(db, authorization[7:])
    except DirectError as error:
        raise HTTPException(error.status, error.code) from error


def install(app):
    if os.getenv("ARCHIVE_COMPAT_READS_ENABLED", "false") != "true":
        return
    from app.routers.wav import router as original

    readonly = APIRouter()
    for route in original.routes:
        if route.methods == {"GET"}:
            readonly.routes.append(route)
    app.include_router(readonly, prefix="/api/v1")

    @app.get("/api/v1/direct-ingest/segments")
    def segments(request: Request):
        resources = direct.resources()
        if not resources:
            raise HTTPException(503, "direct_reader_unavailable")
        with Session(resources[0]) as db:
            actor = device(request, db)
            rows = (
                db.query(Segment, Revision)
                .join(
                    Revision,
                    (Revision.segment_id == Segment.id)
                    & (Revision.revision == Segment.published_revision),
                )
                .filter(Segment.device_id == actor.id)
                .order_by(Segment.bucket.desc())
                .limit(100)
                .all()
            )
            bucket = resources[1].settings.s3_bucket
            keys = [run["key"] for _, revision in rows for run in revision.manifest["runs"]]
            try:
                available = verified_objects("direct", bucket, keys[:1000])
            except ArchiveBlocked as error:
                raise HTTPException(503, "verified_archive_unavailable") from error
            return [
                {
                    "segment_id": segment.id,
                    "sealed": segment.sealed,
                    "raw_available": revision.raw_deleted_at is None
                    or all(run["key"] in available for run in revision.manifest["runs"]),
                    "manifest": revision.manifest,
                    "manifest_sha256": revision.manifest_sha256,
                }
                for segment, revision in rows
            ]

    @app.get("/api/v1/direct-ingest/segments/{segment_id}/runs/{run_index}")
    def run(segment_id: str, run_index: int, request: Request):
        resources = direct.resources()
        if not resources:
            raise HTTPException(503, "direct_reader_unavailable")
        with Session(resources[0]) as db:
            actor = device(request, db)
            segment = db.query(Segment).filter_by(id=segment_id, device_id=actor.id).first()
            revision = (
                db.get(Revision, (segment.id, segment.published_revision)) if segment else None
            )
            if revision is None or not 0 <= run_index < len(revision.manifest["runs"]):
                raise HTTPException(404, "run_not_found")
            selected = revision.manifest["runs"][run_index]
            expected = selected["sha256"]
        try:
            payload = resources[1].get(selected["key"])
        except Exception as error:
            raise HTTPException(503, "verified_archive_unavailable") from error
        if hashlib.sha256(payload).hexdigest() != expected:
            raise HTTPException(503, "artifact_integrity_failed")
        return Response(
            payload, media_type="audio/wav", headers={"Cache-Control": "private, no-store"}
        )
