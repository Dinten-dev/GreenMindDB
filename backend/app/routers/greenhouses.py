"""Greenhouse management endpoints."""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from pydantic import BaseModel

from app.database import get_db
from app.models.user import User
from app.models.master import Greenhouse, Device, Sensor
from app.models.timeseries import SensorReading
from app.auth import get_current_user

router = APIRouter(prefix="/api/greenhouses", tags=["greenhouses"])


class GreenhouseCreate(BaseModel):
    name: str
    location: Optional[str] = None


class GreenhouseResponse(BaseModel):
    id: str
    name: str
    location: Optional[str] = None
    created_at: str
    device_count: int = 0
    sensor_count: int = 0

    class Config:
        from_attributes = True


class GreenhouseOverview(BaseModel):
    id: str
    name: str
    total_devices: int
    online_devices: int
    total_sensors: int
    readings_24h: int


def _require_org(user: User):
    if not user.organization_id:
        raise HTTPException(status_code=400, detail="User must belong to an organization")
    return user.organization_id


@router.get("", response_model=List[GreenhouseResponse])
async def list_greenhouses(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all greenhouses in user's organization."""
    org_id = _require_org(current_user)
    greenhouses = (
        db.query(Greenhouse)
        .filter(Greenhouse.organization_id == org_id)
        .all()
    )
    results = []
    for gh in greenhouses:
        device_count = db.query(func.count(Device.id)).filter(Device.greenhouse_id == gh.id).scalar()
        sensor_count = (
            db.query(func.count(Sensor.id))
            .join(Device, Device.id == Sensor.device_id)
            .filter(Device.greenhouse_id == gh.id)
            .scalar()
        )
        results.append(GreenhouseResponse(
            id=str(gh.id),
            name=gh.name,
            location=gh.location,
            created_at=gh.created_at.isoformat(),
            device_count=device_count,
            sensor_count=sensor_count,
        ))
    return results


@router.post("", response_model=GreenhouseResponse, status_code=201)
async def create_greenhouse(
    data: GreenhouseCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new greenhouse in the user's organization."""
    org_id = _require_org(current_user)
    gh = Greenhouse(
        organization_id=org_id,
        name=data.name,
        location=data.location,
    )
    db.add(gh)
    db.commit()
    db.refresh(gh)
    return GreenhouseResponse(
        id=str(gh.id),
        name=gh.name,
        location=gh.location,
        created_at=gh.created_at.isoformat(),
    )


@router.get("/{greenhouse_id}", response_model=GreenhouseResponse)
async def get_greenhouse(
    greenhouse_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get a single greenhouse."""
    org_id = _require_org(current_user)
    gh = (
        db.query(Greenhouse)
        .filter(Greenhouse.id == greenhouse_id, Greenhouse.organization_id == org_id)
        .first()
    )
    if not gh:
        raise HTTPException(status_code=404, detail="Greenhouse not found")
    device_count = db.query(func.count(Device.id)).filter(Device.greenhouse_id == gh.id).scalar()
    sensor_count = (
        db.query(func.count(Sensor.id))
        .join(Device, Device.id == Sensor.device_id)
        .filter(Device.greenhouse_id == gh.id)
        .scalar()
    )
    return GreenhouseResponse(
        id=str(gh.id),
        name=gh.name,
        location=gh.location,
        created_at=gh.created_at.isoformat(),
        device_count=device_count,
        sensor_count=sensor_count,
    )


@router.get("/{greenhouse_id}/overview", response_model=GreenhouseOverview)
async def get_greenhouse_overview(
    greenhouse_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get overview stats for a greenhouse."""
    org_id = _require_org(current_user)
    gh = (
        db.query(Greenhouse)
        .filter(Greenhouse.id == greenhouse_id, Greenhouse.organization_id == org_id)
        .first()
    )
    if not gh:
        raise HTTPException(status_code=404, detail="Greenhouse not found")

    devices = db.query(Device).filter(Device.greenhouse_id == gh.id).all()
    device_ids = [d.id for d in devices]

    total_sensors = 0
    sensor_ids = []
    if device_ids:
        sensors = db.query(Sensor).filter(Sensor.device_id.in_(device_ids)).all()
        total_sensors = len(sensors)
        sensor_ids = [s.id for s in sensors]

    readings_24h = 0
    if sensor_ids:
        from datetime import datetime, timedelta, timezone
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        readings_24h = (
            db.query(func.count())
            .select_from(SensorReading)
            .filter(SensorReading.sensor_id.in_(sensor_ids), SensorReading.timestamp >= cutoff)
            .scalar()
        )

    online_count = sum(1 for d in devices if d.status == "online")

    return GreenhouseOverview(
        id=str(gh.id),
        name=gh.name,
        total_devices=len(devices),
        online_devices=online_count,
        total_sensors=total_sensors,
        readings_24h=readings_24h,
    )
