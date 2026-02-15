"""Media endpoints: presigned upload URL, commit metadata, list objects."""
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.macmini.auth import get_current_user, require_operator_or_admin, resolve_greenhouse_id
from app.macmini.database import get_db
from app.macmini.schemas import (
    MediaCommitRequest,
    ObjectMetaOut,
    PresignRequest,
    PresignResponse,
)
from app.macmini.storage import get_s3_client
from app.macmini.config import get_settings
from app.models import ObjectMeta, User

settings = get_settings()

router = APIRouter(tags=["media"])


@router.post("/v1/media/presign", response_model=PresignResponse)
def presign_upload(
    payload: PresignRequest,
    user: User = Depends(get_current_user),
):
    client = get_s3_client()
    key = f"media/{payload.greenhouse_id}/{uuid4().hex[:8]}_{payload.kind}"
    url = client.generate_presigned_url(
        "put_object",
        Params={"Bucket": settings.s3_bucket, "Key": key, "ContentType": payload.content_type},
        ExpiresIn=3600,
    )
    return PresignResponse(upload_url=url, storage_key=key)


@router.post("/v1/media/commit", response_model=ObjectMetaOut, status_code=201)
def commit_media(
    payload: MediaCommitRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    obj = ObjectMeta(
        time=datetime.now(timezone.utc),
        greenhouse_id=payload.greenhouse_id,
        kind=payload.kind,
        storage_key=payload.storage_key,
        content_type=payload.content_type,
        sha256=payload.sha256,
        size_bytes=payload.size_bytes,
        plant_id=payload.plant_id,
        zone_id=payload.zone_id,
        created_by_user_id=user.id,
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.get("/operator/media", response_model=list[ObjectMetaOut])
def list_media(
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
    plant_id: UUID | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator_or_admin),
):
    gh_id = resolve_greenhouse_id(user)
    q = db.query(ObjectMeta)
    if gh_id:
        q = q.filter(ObjectMeta.greenhouse_id == gh_id)
    if from_ts:
        q = q.filter(ObjectMeta.time >= from_ts)
    if to_ts:
        q = q.filter(ObjectMeta.time < to_ts)
    if plant_id:
        q = q.filter(ObjectMeta.plant_id == plant_id)
    return q.order_by(ObjectMeta.time.desc()).limit(100).all()
