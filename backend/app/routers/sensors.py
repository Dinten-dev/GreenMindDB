"""Sensor data query endpoints."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models.master import Device, Greenhouse, Sensor
from app.models.timeseries import SensorReading
from app.models.user import User
from app.schemas.sensor import DataPoint, SensorDataResponse, SensorResponse

router = APIRouter(prefix="/sensors", tags=["sensors"])


RANGE_MAP = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


@router.get("", response_model=list[SensorResponse])
async def list_sensors(
    greenhouse_id: str | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List sensors, optionally filtered by greenhouse."""
    if not current_user.organization_id:
        return []

    query = (
        db.query(Sensor, Device)
        .join(Device, Device.id == Sensor.device_id)
        .join(Greenhouse, Greenhouse.id == Device.greenhouse_id)
        .filter(Greenhouse.organization_id == current_user.organization_id)
    )
    if greenhouse_id:
        query = query.filter(Device.greenhouse_id == greenhouse_id)

    results = []
    for sensor, device in query.all():
        results.append(
            SensorResponse(
                id=str(sensor.id),
                device_id=str(sensor.device_id),
                kind=sensor.kind,
                unit=sensor.unit,
                label=sensor.label,
                device_serial=device.serial,
                device_name=device.name,
                device_status=device.status,
            )
        )
    return results


@router.get("/{sensor_id}/data", response_model=SensorDataResponse)
async def get_sensor_data(
    sensor_id: str,
    range: str = Query("24h", regex="^(24h|7d|30d)$"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get timeseries data for a sensor, with range filter."""
    if not current_user.organization_id:
        raise HTTPException(status_code=403, detail="No organization")

    # Verify sensor belongs to user's org
    result = (
        db.query(Sensor, Device)
        .join(Device, Device.id == Sensor.device_id)
        .join(Greenhouse, Greenhouse.id == Device.greenhouse_id)
        .filter(
            Sensor.id == sensor_id,
            Greenhouse.organization_id == current_user.organization_id,
        )
        .first()
    )
    if not result:
        raise HTTPException(status_code=404, detail="Sensor not found")

    sensor, device = result
    td = RANGE_MAP[range]
    cutoff = datetime.now(UTC) - td

    # Determine appropriate bucketing
    if range == "24h":
        # Return raw data (or 1-minute buckets if too many points)
        readings = (
            db.query(SensorReading)
            .filter(SensorReading.sensor_id == sensor_id, SensorReading.timestamp >= cutoff)
            .order_by(SensorReading.timestamp.asc())
            .limit(1440)
            .all()
        )
        data = [
            DataPoint(timestamp=r.timestamp.isoformat(), value=round(r.value, 2)) for r in readings
        ]
    else:
        # Use time_bucket for 7d and 30d
        bucket_size = "15 minutes" if range == "7d" else "1 hour"
        rows = db.execute(
            text(
                f"""
                SELECT time_bucket('{bucket_size}', timestamp) AS bucket,
                       AVG(value) AS avg_value
                FROM sensor_reading
                WHERE sensor_id = :sid AND timestamp >= :cutoff
                GROUP BY bucket
                ORDER BY bucket ASC
            """
            ),
            {"sid": sensor_id, "cutoff": cutoff},
        ).fetchall()
        data = [
            DataPoint(timestamp=row.bucket.isoformat(), value=round(row.avg_value, 2))
            for row in rows
        ]

    return SensorDataResponse(
        sensor_id=str(sensor.id),
        kind=sensor.kind,
        unit=sensor.unit,
        label=sensor.label,
        data=data,
    )
