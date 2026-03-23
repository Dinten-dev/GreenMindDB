"""Business logic for greenhouses."""

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.master import Device, Greenhouse, Sensor
from app.models.timeseries import SensorReading
from app.models.user import User
from app.schemas.greenhouse import GreenhouseCreate, GreenhouseOverview, GreenhouseResponse


def _require_org(user: User):
    if not user.organization_id:
        raise HTTPException(status_code=400, detail="User must belong to an organization")
    return user.organization_id


def list_greenhouses(db: Session, user: User) -> list[GreenhouseResponse]:
    org_id = _require_org(user)
    greenhouses = db.query(Greenhouse).filter(Greenhouse.organization_id == org_id).all()
    results = []
    for gh in greenhouses:
        device_count = (
            db.query(func.count(Device.id)).filter(Device.greenhouse_id == gh.id).scalar()
        )
        sensor_count = (
            db.query(func.count(Sensor.id))
            .join(Device, Device.id == Sensor.device_id)
            .filter(Device.greenhouse_id == gh.id)
            .scalar()
        )
        results.append(
            GreenhouseResponse(
                id=str(gh.id),
                name=gh.name,
                location=gh.location,
                created_at=gh.created_at.isoformat(),
                device_count=device_count,
                sensor_count=sensor_count,
            )
        )
    return results


def create_greenhouse(db: Session, user: User, data: GreenhouseCreate) -> GreenhouseResponse:
    org_id = _require_org(user)
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


def get_greenhouse(db: Session, user: User, greenhouse_id: str) -> GreenhouseResponse:
    org_id = _require_org(user)
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


def get_greenhouse_overview(db: Session, user: User, greenhouse_id: str) -> GreenhouseOverview:
    org_id = _require_org(user)
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
        cutoff = datetime.now(UTC) - timedelta(hours=24)
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
