"""Router for public, login-free plant observations."""

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.rate_limit import limiter
from app.schemas.observation import (
    ObservationSessionCreate,
    ObservationSessionResponse,
    PlantObservationCreate,
    PlantObservationPhotoResponse,
    PlantObservationResponse,
    PublicPlantContextResponse,
)
from app.services.observation_service import (
    create_observation,
    create_observation_session,
    get_plant_context,
    upload_observation_photo,
)

# Consider rate limiting this router heavily using slowapi
router = APIRouter(prefix="/public/observe", tags=["public-observe"])


@router.post("/session", response_model=ObservationSessionResponse)
@limiter.limit("10/minute")
def handle_create_session(
    request: Request,
    data: ObservationSessionCreate,
    db: Session = Depends(get_db),
):
    ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    return create_observation_session(db, data.public_id, ip, user_agent)


@router.get("/session/{session_token}/context", response_model=PublicPlantContextResponse)
@limiter.limit("30/minute")
def handle_get_context(
    request: Request,
    session_token: str,
    db: Session = Depends(get_db),
):
    return get_plant_context(db, session_token)


@router.post("/session/{session_token}/observations", response_model=PlantObservationResponse)
@limiter.limit("10/minute")
def handle_create_observation(
    request: Request,
    session_token: str,
    data: PlantObservationCreate,
    db: Session = Depends(get_db),
):
    return create_observation(db, session_token, data)


@router.post(
    "/session/{session_token}/observations/{observation_id}/photos",
    response_model=PlantObservationPhotoResponse,
)
@limiter.limit("10/minute")
def handle_upload_photo(
    request: Request,
    session_token: str,
    observation_id: str,
    file: UploadFile,
    db: Session = Depends(get_db),
):
    if not file.content_type:
        raise HTTPException(status_code=400, detail="Missing content type")
    
    allowed_types = ["image/jpeg", "image/png", "image/webp"]
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="File must be a JPEG, PNG, or WebP image")

    file_size = 0
    file.file.seek(0, 2)
    file_size = file.file.tell()
    file.file.seek(0)

    if file_size > 10 * 1024 * 1024:  # 10 MB limit
        raise HTTPException(status_code=400, detail="File too large")

    return upload_observation_photo(
        db,
        session_token,
        observation_id,
        file.file,
        file.content_type,
        file_size,
    )
