"""Sensor (ESP32) management and data query endpoints."""

import io
import zipfile
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import get_current_user, verify_password
from app.database import get_db
from app.models.master import Gateway, Sensor, Zone
from app.models.timeseries import SensorReading
from app.models.user import User
from app.rate_limit import limiter
from app.schemas.gateway import PairingCodeRequest, PairingCodeResponse
from app.schemas.sensor import (
    ClaimSensorRequest,
    ClaimSensorResponse,
    DataPoint,
    MoveSensorRequest,
    SensorDataResponse,
    SensorResponse,
)
from app.services.gateway_service import gateway_commands_cache, generate_pairing_code

router = APIRouter(prefix="/sensors", tags=["sensors"])

RANGE_MAP = {
    "5m": timedelta(minutes=5),
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}

LIVENESS_THRESHOLD = timedelta(minutes=5)


# ── List Sensors ────────────────────────────────────


@router.get("", response_model=list[SensorResponse])
async def list_sensors(
    zone_id: str | None = Query(None),
    gateway_id: str | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List sensors, optionally filtered by zone or gateway."""
    if not current_user.organization_id:
        return []

    query = (
        db.query(Sensor, Gateway)
        .join(Gateway, Gateway.id == Sensor.gateway_id)
        .join(Zone, Zone.id == Gateway.zone_id)
        .filter(Zone.organization_id == current_user.organization_id)
    )
    if zone_id:
        query = query.filter(Gateway.zone_id == zone_id)
    if gateway_id:
        query = query.filter(Sensor.gateway_id == gateway_id)

    now = datetime.now(UTC)
    results = []
    for sensor, gw in query.all():
        is_online = bool(sensor.last_seen and (now - sensor.last_seen) < LIVENESS_THRESHOLD)
        results.append(
            SensorResponse(
                id=str(sensor.id),
                gateway_id=str(sensor.gateway_id),
                zone_id=str(gw.zone_id),
                mac_address=sensor.mac_address,
                name=sensor.name,
                sensor_type=sensor.sensor_type,
                status="online" if is_online else "offline",
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


@router.post("/pairing-code", response_model=PairingCodeResponse, status_code=201)
@limiter.limit("5/minute")
async def handle_generate_pairing_code(
    request: Request,
    data: PairingCodeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Generate a short-lived pairing code for a sensor to use in its Captive Portal."""
    return generate_pairing_code(db, current_user, data.zone_id)


# ── Move Sensor ─────────────────────────────────────


@router.patch("/{sensor_id}/move", response_model=SensorResponse)
async def move_sensor(
    sensor_id: str,
    data: MoveSensorRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Move a sensor to a different gateway within the same zone."""
    if not current_user.organization_id:
        raise HTTPException(status_code=403, detail="No organization")

    # Find sensor and verify ownership
    result = (
        db.query(Sensor, Gateway)
        .join(Gateway, Gateway.id == Sensor.gateway_id)
        .join(Zone, Zone.id == Gateway.zone_id)
        .filter(
            Sensor.id == sensor_id,
            Zone.organization_id == current_user.organization_id,
        )
        .first()
    )
    if not result:
        raise HTTPException(status_code=404, detail="Sensor not found")

    sensor, current_gw = result

    # Verify target gateway belongs to same zone
    target_gw = (
        db.query(Gateway)
        .filter(
            Gateway.id == data.target_gateway_id,
            Gateway.zone_id == current_gw.zone_id,
        )
        .first()
    )
    if not target_gw:
        raise HTTPException(
            status_code=400,
            detail="Target gateway not found or not in the same zone",
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


# ── Delete Sensor ───────────────────────────────────


@router.delete("/{sensor_id}", status_code=204)
async def delete_sensor(
    sensor_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a sensor and all its readings."""
    if not current_user.organization_id:
        raise HTTPException(status_code=403, detail="No organization")

    result = (
        db.query(Sensor, Gateway)
        .join(Gateway, Gateway.id == Sensor.gateway_id)
        .join(Zone, Zone.id == Gateway.zone_id)
        .filter(
            Sensor.id == sensor_id,
            Zone.organization_id == current_user.organization_id,
        )
        .first()
    )
    if not result:
        raise HTTPException(status_code=404, detail="Sensor not found")

    sensor, _ = result

    # Delete readings first (no FK cascade on hypertable)
    db.execute(
        text("DELETE FROM sensor_reading WHERE sensor_id = :sid"),
        {"sid": str(sensor.id)},
    )

    # Send remote delete command to gateway
    gw_id = str(sensor.gateway_id)
    if gw_id not in gateway_commands_cache:
        gateway_commands_cache[gw_id] = []
    gateway_commands_cache[gw_id].append(
        {"action": "delete_sensor", "mac_address": sensor.mac_address}
    )

    db.delete(sensor)
    db.commit()


# ── Sensor Data ─────────────────────────────────────

RESOLUTION_BUCKET_MAP = {
    "1m": "1 minute",
    "5m": "5 minutes",
    "1h": "1 hour",
    "1d": "1 day",
}


@router.get("/{sensor_id}/data", response_model=list[SensorDataResponse])
async def get_sensor_data(
    sensor_id: str,
    range: str = Query("24h", pattern="^(5m|1h|24h|7d|30d)$"),
    resolution: str | None = Query(None, pattern="^(raw|1m|5m|1h|1d)$"),
    date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get timeseries data for a sensor, grouped by kind.

    Supports:
    - range: relative window (1h, 24h, 7d, 30d)
    - date: specific day (YYYY-MM-DD), overrides range
    - resolution: aggregation bucket (raw, 1m, 5m, 1h, 1d)
    """
    if not current_user.organization_id:
        raise HTTPException(status_code=403, detail="No organization")

    result = (
        db.query(Sensor, Gateway)
        .join(Gateway, Gateway.id == Sensor.gateway_id)
        .join(Zone, Zone.id == Gateway.zone_id)
        .filter(
            Sensor.id == sensor_id,
            Zone.organization_id == current_user.organization_id,
        )
        .first()
    )
    if not result:
        raise HTTPException(status_code=404, detail="Sensor not found")

    # Determine time window
    if date:
        from datetime import date as date_type

        try:
            day = date_type.fromisoformat(date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format") from None
        start = datetime(day.year, day.month, day.day, tzinfo=UTC)
        end = start + timedelta(days=1)
    else:
        td = RANGE_MAP[range]
        end = datetime.now(UTC)
        start = end - td

    # Auto-pick resolution if not specified
    if resolution is None:
        span = end - start
        if span <= timedelta(hours=1):
            resolution = "raw"
        elif span <= timedelta(hours=24):
            resolution = "1m"
        elif span <= timedelta(days=7):
            resolution = "5m"
        elif span <= timedelta(days=30):
            resolution = "1h"
        else:
            resolution = "1d"

    # Get distinct kinds
    kinds = (
        db.query(SensorReading.kind)
        .filter(
            SensorReading.sensor_id == sensor_id,
            SensorReading.timestamp >= start,
            SensorReading.timestamp < end,
        )
        .distinct()
        .all()
    )

    responses = []
    for (kind,) in kinds:
        if resolution == "raw":
            readings = (
                db.query(SensorReading)
                .filter(
                    SensorReading.sensor_id == sensor_id,
                    SensorReading.kind == kind,
                    SensorReading.timestamp >= start,
                    SensorReading.timestamp < end,
                )
                .order_by(SensorReading.timestamp.desc())
                .limit(5000)
                .all()
            )
            readings.reverse()  # Back to chronological order
            data = [
                DataPoint(timestamp=r.timestamp.isoformat(), value=round(r.value, 2))
                for r in readings
            ]
        else:
            bucket_size = RESOLUTION_BUCKET_MAP[resolution]
            rows = db.execute(
                text(f"""
                    SELECT time_bucket('{bucket_size}', timestamp) AS bucket,
                           AVG(value) AS avg_value
                    FROM sensor_reading
                    WHERE sensor_id = :sid AND kind = :kind
                      AND timestamp >= :start AND timestamp < :end_ts
                    GROUP BY bucket
                    ORDER BY bucket ASC
                """),
                {"sid": sensor_id, "kind": kind, "start": start, "end_ts": end},
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


# ── Sensor Data Export (ZIP) ────────────────────────


EXPORT_RANGE_MAP = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "all": timedelta(days=365 * 10),
}

BATCH_SIZE = 10_000


@router.get("/{sensor_id}/export")
async def export_sensor_data(
    sensor_id: str,
    range: str = Query("24h", pattern="^(1h|24h|7d|30d|all)$"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Export sensor data as a ZIP archive containing one CSV per measurement kind."""
    if not current_user.organization_id:
        raise HTTPException(status_code=403, detail="No organization")

    # Verify sensor ownership
    result = (
        db.query(Sensor, Gateway, Zone)
        .join(Gateway, Gateway.id == Sensor.gateway_id)
        .join(Zone, Zone.id == Gateway.zone_id)
        .filter(
            Sensor.id == sensor_id,
            Zone.organization_id == current_user.organization_id,
        )
        .first()
    )
    if not result:
        raise HTTPException(status_code=404, detail="Sensor not found")

    sensor, gw, zone = result
    td = EXPORT_RANGE_MAP[range]
    cutoff = datetime.now(UTC) - td

    # Get distinct kinds
    kinds = (
        db.query(SensorReading.kind)
        .filter(SensorReading.sensor_id == sensor_id, SensorReading.timestamp >= cutoff)
        .distinct()
        .all()
    )

    if not kinds:
        raise HTTPException(status_code=404, detail="No data available for export")

    # Build zone metadata header
    zone_meta = (
        f"# Zone: {zone.name}\n"
        f"# Type: {zone.zone_type}\n"
        f"# Location: {zone.location or '—'}\n"
    )
    if zone.latitude is not None and zone.longitude is not None:
        zone_meta += f"# GPS: {zone.latitude}, {zone.longitude}\n"
    zone_meta += f"# Gateway: {gw.name or gw.hardware_id}\n"
    zone_meta += f"# Sensor: {sensor.name or sensor.mac_address}\n"

    # Build ZIP in memory using streaming writes per kind
    zip_buffer = io.BytesIO()
    sensor_label = sensor.name or sensor.mac_address

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for (kind,) in kinds:
            csv_buffer = io.StringIO()
            csv_buffer.write(zone_meta)
            csv_buffer.write("timestamp,value,unit\n")

            offset = 0
            while True:
                batch = (
                    db.query(SensorReading)
                    .filter(
                        SensorReading.sensor_id == sensor_id,
                        SensorReading.kind == kind,
                        SensorReading.timestamp >= cutoff,
                    )
                    .order_by(SensorReading.timestamp.asc())
                    .offset(offset)
                    .limit(BATCH_SIZE)
                    .all()
                )

                for reading in batch:
                    csv_buffer.write(
                        f"{reading.timestamp.isoformat()},"
                        f"{round(reading.value, 4)},"
                        f"{reading.unit}\n"
                    )

                if len(batch) < BATCH_SIZE:
                    break
                offset += BATCH_SIZE

            zf.writestr(f"{kind}.csv", csv_buffer.getvalue())
            csv_buffer.close()

    zip_buffer.seek(0)
    filename = f"greenmind_{sensor_label}_{range}_{datetime.now(UTC).strftime('%Y%m%d')}.zip"

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Helpers ─────────────────────────────────────────


def _authenticate_gateway(db: Session, api_key: str) -> Gateway:
    """Find and authenticate a gateway by its API key (brute search)."""
    gateways = db.query(Gateway).filter(Gateway.is_active.is_(True)).all()
    for gw in gateways:
        if gw.api_key_hash and verify_password(api_key, gw.api_key_hash):
            return gw
    raise HTTPException(status_code=401, detail="Invalid API key")
