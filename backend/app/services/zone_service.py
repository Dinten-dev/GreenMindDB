"""Business logic for zones (agriculture areas)."""

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.master import Gateway, Sensor, Zone
from app.models.timeseries import SensorReading
from app.models.user import User
from app.schemas.zone import ZoneCreate, ZoneOverview, ZoneResponse


def _require_org(user: User):
    if not user.organization_id:
        raise HTTPException(status_code=400, detail="User must belong to an organization")
    return user.organization_id


def list_zones(db: Session, user: User) -> list[ZoneResponse]:
    org_id = _require_org(user)
    zones = db.query(Zone).filter(Zone.organization_id == org_id).all()
    results = []
    for z in zones:
        gateway_count = db.query(func.count(Gateway.id)).filter(Gateway.zone_id == z.id).scalar()
        sensor_count = (
            db.query(func.count(Sensor.id))
            .join(Gateway, Gateway.id == Sensor.gateway_id)
            .filter(Gateway.zone_id == z.id)
            .scalar()
        )
        results.append(
            ZoneResponse(
                id=str(z.id),
                name=z.name,
                location=z.location,
                zone_type=z.zone_type,
                latitude=z.latitude,
                longitude=z.longitude,
                created_at=z.created_at.isoformat(),
                gateway_count=gateway_count,
                sensor_count=sensor_count,
            )
        )
    return results


def create_zone(db: Session, user: User, data: ZoneCreate) -> ZoneResponse:
    org_id = _require_org(user)
    z = Zone(
        organization_id=org_id,
        name=data.name,
        location=data.location,
        zone_type=data.zone_type,
        latitude=data.latitude,
        longitude=data.longitude,
    )
    db.add(z)
    db.commit()
    db.refresh(z)
    return ZoneResponse(
        id=str(z.id),
        name=z.name,
        location=z.location,
        zone_type=z.zone_type,
        latitude=z.latitude,
        longitude=z.longitude,
        created_at=z.created_at.isoformat(),
    )


def get_zone(db: Session, user: User, zone_id: str) -> ZoneResponse:
    org_id = _require_org(user)
    z = db.query(Zone).filter(Zone.id == zone_id, Zone.organization_id == org_id).first()
    if not z:
        raise HTTPException(status_code=404, detail="Zone not found")
    gateway_count = db.query(func.count(Gateway.id)).filter(Gateway.zone_id == z.id).scalar()
    sensor_count = (
        db.query(func.count(Sensor.id))
        .join(Gateway, Gateway.id == Sensor.gateway_id)
        .filter(Gateway.zone_id == z.id)
        .scalar()
    )
    return ZoneResponse(
        id=str(z.id),
        name=z.name,
        location=z.location,
        zone_type=z.zone_type,
        latitude=z.latitude,
        longitude=z.longitude,
        created_at=z.created_at.isoformat(),
        gateway_count=gateway_count,
        sensor_count=sensor_count,
    )


def get_zone_overview(db: Session, user: User, zone_id: str) -> ZoneOverview:
    org_id = _require_org(user)
    z = db.query(Zone).filter(Zone.id == zone_id, Zone.organization_id == org_id).first()
    if not z:
        raise HTTPException(status_code=404, detail="Zone not found")

    gateways = db.query(Gateway).filter(Gateway.zone_id == z.id).all()
    gateway_ids = [g.id for g in gateways]

    total_sensors = 0
    sensor_ids = []
    if gateway_ids:
        sensors = db.query(Sensor).filter(Sensor.gateway_id.in_(gateway_ids)).all()
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

    online_count = sum(1 for g in gateways if g.status == "online")

    return ZoneOverview(
        id=str(z.id),
        name=z.name,
        zone_type=z.zone_type,
        total_gateways=len(gateways),
        online_gateways=online_count,
        total_sensors=total_sensors,
        readings_24h=readings_24h,
    )


def delete_zone(db: Session, user: User, zone_id: str) -> None:
    org_id = _require_org(user)
    z = db.query(Zone).filter(Zone.id == zone_id, Zone.organization_id == org_id).first()
    if not z:
        raise HTTPException(status_code=404, detail="Zone not found")

    # Find all sensors in this zone to delete their readings first (no FK cascade)
    gateways = db.query(Gateway.id).filter(Gateway.zone_id == z.id).all()
    if gateways:
        gw_ids = [g[0] for g in gateways]
        sensors = db.query(Sensor.id).filter(Sensor.gateway_id.in_(gw_ids)).all()
        if sensors:
            sensor_ids_str = [str(s[0]) for s in sensors]
            from sqlalchemy import text

            db.execute(
                text("DELETE FROM sensor_reading WHERE sensor_id = ANY(:sids)"),
                {"sids": sensor_ids_str},
            )

    db.delete(z)
    db.commit()
