"""Sensor (ESP32) management and data query endpoints."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import get_current_user, verify_password
from app.database import get_db
from app.models.master import Gateway, Greenhouse, Sensor
from app.models.timeseries import SensorReading
from app.models.user import User
from app.schemas.sensor import (
    ClaimSensorRequest,
    ClaimSensorResponse,
    DataPoint,
    MoveSensorRequest,
    SensorDataResponse,
    SensorResponse,
)

router = APIRouter(prefix="/sensors", tags=["sensors"])

RANGE_MAP = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


# ── List Sensors ────────────────────────────────────


@router.get("", response_model=list[SensorResponse])
async def list_sensors(
    greenhouse_id: str | None = Query(None),
    gateway_id: str | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List sensors, optionally filtered by greenhouse or gateway."""
    if not current_user.organization_id:
        return []

    query = (
        db.query(Sensor, Gateway)
        .join(Gateway, Gateway.id == Sensor.gateway_id)
        .join(Greenhouse, Greenhouse.id == Gateway.greenhouse_id)
        .filter(Greenhouse.organization_id == current_user.organization_id)
    )
    if greenhouse_id:
        query = query.filter(Gateway.greenhouse_id == greenhouse_id)
    if gateway_id:
        query = query.filter(Sensor.gateway_id == gateway_id)

    results = []
    for sensor, gw in query.all():
        results.append(
            SensorResponse(
                id=str(sensor.id),
                gateway_id=str(sensor.gateway_id),
                mac_address=sensor.mac_address,
                name=sensor.name,
                sensor_type=sensor.sensor_type,
                status=sensor.status,
                last_seen=sensor.last_seen.isoformat() if sensor.last_seen else None,
                claimed_at=sensor.claimed_at.isoformat() if sensor.claimed_at else None,
                gateway_name=gw.name,
                gateway_hardware_id=gw.hardware_id,
            )
        )
    return results


# ── Claim Sensor (Gateway → Cloud) ─────────────────


@router.post("/claim", response_model=ClaimSensorResponse, status_code=201)
async def claim_sensor(
    data: ClaimSensorRequest,
    db: Session = Depends(get_db),
    x_api_key: str | None = Header(None),
):
    """Gateway claims a discovered ESP32 sensor by MAC address."""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-Api-Key header")

    # Find the gateway by API key
    gateway = _authenticate_gateway(db, x_api_key)

    # Check if MAC already claimed
    existing = db.query(Sensor).filter(Sensor.mac_address == data.mac_address).first()
    if existing:
        raise HTTPException(status_code=409, detail="Sensor MAC already registered")

    sensor = Sensor(
        gateway_id=gateway.id,
        mac_address=data.mac_address,
        name=data.name or data.mac_address,
        sensor_type=data.sensor_type,
        status="online",
        last_seen=datetime.now(UTC),
    )
    db.add(sensor)
    db.commit()
    db.refresh(sensor)

    return ClaimSensorResponse(
        sensor_id=str(sensor.id),
        mac_address=sensor.mac_address,
        gateway_id=str(gateway.id),
    )


# ── Move Sensor ─────────────────────────────────────


@router.patch("/{sensor_id}/move", response_model=SensorResponse)
async def move_sensor(
    sensor_id: str,
    data: MoveSensorRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Move a sensor to a different gateway within the same greenhouse."""
    if not current_user.organization_id:
        raise HTTPException(status_code=403, detail="No organization")

    # Find sensor and verify ownership
    result = (
        db.query(Sensor, Gateway)
        .join(Gateway, Gateway.id == Sensor.gateway_id)
        .join(Greenhouse, Greenhouse.id == Gateway.greenhouse_id)
        .filter(
            Sensor.id == sensor_id,
            Greenhouse.organization_id == current_user.organization_id,
        )
        .first()
    )
    if not result:
        raise HTTPException(status_code=404, detail="Sensor not found")

    sensor, current_gw = result

    # Verify target gateway belongs to same greenhouse
    target_gw = (
        db.query(Gateway)
        .filter(
            Gateway.id == data.target_gateway_id,
            Gateway.greenhouse_id == current_gw.greenhouse_id,
        )
        .first()
    )
    if not target_gw:
        raise HTTPException(
            status_code=400,
            detail="Target gateway not found or not in the same greenhouse",
        )

    sensor.gateway_id = target_gw.id
    db.commit()
    db.refresh(sensor)

    return SensorResponse(
        id=str(sensor.id),
        gateway_id=str(sensor.gateway_id),
        mac_address=sensor.mac_address,
        name=sensor.name,
        sensor_type=sensor.sensor_type,
        status=sensor.status,
        last_seen=sensor.last_seen.isoformat() if sensor.last_seen else None,
        claimed_at=sensor.claimed_at.isoformat() if sensor.claimed_at else None,
        gateway_name=target_gw.name,
        gateway_hardware_id=target_gw.hardware_id,
    )


# ── Sensor Data ─────────────────────────────────────


@router.get("/{sensor_id}/data", response_model=list[SensorDataResponse])
async def get_sensor_data(
    sensor_id: str,
    range: str = Query("24h", regex="^(1h|24h|7d|30d)$"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get timeseries data for a sensor, grouped by kind, with range filter."""
    if not current_user.organization_id:
        raise HTTPException(status_code=403, detail="No organization")

    # Verify sensor belongs to user's org
    result = (
        db.query(Sensor, Gateway)
        .join(Gateway, Gateway.id == Sensor.gateway_id)
        .join(Greenhouse, Greenhouse.id == Gateway.greenhouse_id)
        .filter(
            Sensor.id == sensor_id,
            Greenhouse.organization_id == current_user.organization_id,
        )
        .first()
    )
    if not result:
        raise HTTPException(status_code=404, detail="Sensor not found")

    td = RANGE_MAP[range]
    cutoff = datetime.now(UTC) - td

    # Get distinct kinds for this sensor
    kinds = (
        db.query(SensorReading.kind)
        .filter(SensorReading.sensor_id == sensor_id, SensorReading.timestamp >= cutoff)
        .distinct()
        .all()
    )

    responses = []
    for (kind,) in kinds:
        if range == "24h":
            readings = (
                db.query(SensorReading)
                .filter(
                    SensorReading.sensor_id == sensor_id,
                    SensorReading.kind == kind,
                    SensorReading.timestamp >= cutoff,
                )
                .order_by(SensorReading.timestamp.asc())
                .limit(1440)
                .all()
            )
            data = [
                DataPoint(timestamp=r.timestamp.isoformat(), value=round(r.value, 2))
                for r in readings
            ]
        else:
            bucket_size = "15 minutes" if range == "7d" else "1 hour"
            rows = db.execute(
                text(
                    f"""
                    SELECT time_bucket('{bucket_size}', timestamp) AS bucket,
                           AVG(value) AS avg_value
                    FROM sensor_reading
                    WHERE sensor_id = :sid AND kind = :kind AND timestamp >= :cutoff
                    GROUP BY bucket
                    ORDER BY bucket ASC
                """
                ),
                {"sid": sensor_id, "kind": kind, "cutoff": cutoff},
            ).fetchall()
            data = [
                DataPoint(timestamp=row.bucket.isoformat(), value=round(row.avg_value, 2))
                for row in rows
            ]

        # Get unit from latest reading
        latest = (
            db.query(SensorReading.unit)
            .filter(SensorReading.sensor_id == sensor_id, SensorReading.kind == kind)
            .order_by(SensorReading.timestamp.desc())
            .first()
        )
        unit = latest[0] if latest else ""

        responses.append(
            SensorDataResponse(
                sensor_id=str(sensor_id),
                kind=kind,
                unit=unit,
                data=data,
            )
        )

    return responses


# ── Helpers ─────────────────────────────────────────


def _authenticate_gateway(db: Session, api_key: str) -> Gateway:
    """Find and authenticate a gateway by its API key (brute search)."""
    gateways = db.query(Gateway).filter(Gateway.is_active.is_(True)).all()
    for gw in gateways:
        if gw.api_key_hash and verify_password(api_key, gw.api_key_hash):
            return gw
    raise HTTPException(status_code=401, detail="Invalid API key")
