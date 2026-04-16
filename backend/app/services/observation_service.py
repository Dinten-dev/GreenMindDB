"""Business logic for Plant Observation & Public QR Access."""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import BinaryIO

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.models.observation import (
    PlantObservation,
    PlantObservationAccess,
    PlantObservationPhoto,
    PlantObservationSession,
)
from app.models.plant import Plant, PlantSensorAssignment
from app.models.master import Zone
from app.schemas.observation import (
    ObservationSessionResponse,
    PlantObservationCreate,
    PlantObservationPhotoResponse,
    PlantObservationResponse,
    PublicPlantContextResponse,
)

logger = logging.getLogger(__name__)

# Lazy-initialized S3 client
_s3_client = None
_PHOTO_BUCKET = "greenmind-photos"

def _get_s3_client():
    global _s3_client
    if _s3_client is not None:
        return _s3_client

    _s3_client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        region_name=settings.s3_region,
        config=BotoConfig(signature_version="s3v4"),
    )

    try:
        _s3_client.head_bucket(Bucket=_PHOTO_BUCKET)
    except ClientError:
        logger.info("Creating S3 bucket: %s", _PHOTO_BUCKET)
        _s3_client.create_bucket(Bucket=_PHOTO_BUCKET)

    return _s3_client


def create_observation_session(db: Session, public_id: str, ip: str, user_agent: str) -> ObservationSessionResponse:
    try:
        uuid_obj = uuid.UUID(public_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid public ID format")

    access = db.query(PlantObservationAccess).filter(
        PlantObservationAccess.public_id == uuid_obj,
        PlantObservationAccess.is_active == True
    ).first()

    if not access:
        raise HTTPException(status_code=404, detail="Observation access not found or revoked")

    # Access is valid. Increment usage and timestamp.
    access.usage_count += 1
    access.last_used_at = datetime.now(UTC)

    # Create session (TTL: 30 minutes)
    session_token = str(uuid.uuid4())
    expires_at = datetime.now(UTC) + timedelta(minutes=30)
    
    session = PlantObservationSession(
        plant_id=access.plant_id,
        access_id=access.id,
        session_token=session_token,
        expires_at=expires_at,
        used_ip=ip,
        user_agent=user_agent,
        is_active=True
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    
    return ObservationSessionResponse(
        session_token=session.session_token,
        expires_at=session.expires_at.isoformat()
    )


def _get_valid_session(db: Session, session_token: str) -> PlantObservationSession:
    session = db.query(PlantObservationSession).filter(
        PlantObservationSession.session_token == session_token,
        PlantObservationSession.is_active == True
    ).first()
    
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session token")
        
    if datetime.now(UTC) > session.expires_at.replace(tzinfo=UTC):
        session.is_active = False
        db.commit()
        raise HTTPException(status_code=401, detail="Session expired")
        
    return session


def get_plant_context(db: Session, session_token: str) -> PublicPlantContextResponse:
    session = _get_valid_session(db, session_token)
    plant = db.query(Plant).filter(Plant.id == session.plant_id).first()
    zone = db.query(Zone).filter(Zone.id == plant.zone_id).first()
    
    return PublicPlantContextResponse(
        plant_id=str(plant.id),
        name=plant.name,
        plant_code=plant.plant_code,
        zone_name=zone.name if zone else None
    )


def create_observation(db: Session, session_token: str, data: PlantObservationCreate) -> PlantObservationResponse:
    session = _get_valid_session(db, session_token)
    plant = db.query(Plant).filter(Plant.id == session.plant_id).first()
    
    active_assignment = (
        db.query(PlantSensorAssignment)
        .filter(
            PlantSensorAssignment.plant_id == plant.id,
            PlantSensorAssignment.is_active == True
        )
        .first()
    )
    sensor_id = active_assignment.sensor_id if active_assignment else None

    obs = PlantObservation(
        plant_id=plant.id,
        sensor_id=sensor_id,
        zone_id=plant.zone_id,
        observation_session_id=session.id,
        wellbeing_score=data.wellbeing_score,
        stress_score=data.stress_score,
        plant_condition=data.plant_condition,
        leaf_droop=data.leaf_droop,
        leaf_color=data.leaf_color,
        spots_present=data.spots_present,
        soil_condition=data.soil_condition,
        suspected_stress_type=data.suspected_stress_type,
        notes=data.notes,
    )
    db.add(obs)
    db.commit()
    db.refresh(obs)
    
    return get_observation(db, str(obs.id))


def upload_observation_photo(
    db: Session, 
    session_token: str, 
    observation_id: str, 
    file_data: BinaryIO, 
    mime_type: str, 
    file_size: int
) -> PlantObservationPhotoResponse:
    session = _get_valid_session(db, session_token)
    
    obs = db.query(PlantObservation).filter(
        PlantObservation.id == observation_id,
        PlantObservation.plant_id == session.plant_id
    ).first()
    if not obs:
        raise HTTPException(status_code=404, detail="Observation not found in this session")

    # Generate key
    date_str = datetime.now(UTC).strftime("%Y%m%d")
    time_str = datetime.now(UTC).strftime("%H%M%S")
    file_ext = "jpg" if "jpeg" in mime_type or "jpg" in mime_type else "png"
    s3_key = f"photos/plant/{session.plant_id}/{date_str}/{time_str}_{uuid.uuid4().hex[:8]}.{file_ext}"

    # Upload to MinIO
    client = _get_s3_client()
    client.upload_fileobj(
        file_data,
        _PHOTO_BUCKET,
        s3_key,
        ExtraArgs={"ContentType": mime_type},
    )

    photo = PlantObservationPhoto(
        observation_id=obs.id,
        storage_key=s3_key,
        mime_type=mime_type,
        file_size=file_size
    )
    db.add(photo)
    db.commit()
    db.refresh(photo)

    return PlantObservationPhotoResponse(
        id=str(photo.id),
        observation_id=str(photo.observation_id),
        storage_key=photo.storage_key,
        mime_type=photo.mime_type,
        file_size=photo.file_size,
        created_at=photo.created_at.isoformat()
    )


def get_observation(db: Session, observation_id: str) -> PlantObservationResponse:
    obs = db.query(PlantObservation).filter(PlantObservation.id == observation_id).first()
    if not obs:
        raise HTTPException(status_code=404, detail="Observation not found")
        
    photos = db.query(PlantObservationPhoto).filter(PlantObservationPhoto.observation_id == obs.id).all()
    photo_responses = [
        PlantObservationPhotoResponse(
            id=str(p.id),
            observation_id=str(p.observation_id),
            storage_key=p.storage_key,
            mime_type=p.mime_type,
            file_size=p.file_size,
            created_at=p.created_at.isoformat()
        )
        for p in photos
    ]
    
    return PlantObservationResponse(
        id=str(obs.id),
        plant_id=str(obs.plant_id),
        sensor_id=str(obs.sensor_id) if obs.sensor_id else None,
        zone_id=str(obs.zone_id),
        observed_at=obs.observed_at.isoformat(),
        wellbeing_score=obs.wellbeing_score,
        stress_score=obs.stress_score,
        plant_condition=obs.plant_condition,
        leaf_droop=obs.leaf_droop,
        leaf_color=obs.leaf_color,
        spots_present=obs.spots_present,
        soil_condition=obs.soil_condition,
        suspected_stress_type=obs.suspected_stress_type,
        notes=obs.notes,
        created_at=obs.created_at.isoformat(),
        photos=photo_responses
    )
