"""Sensor (ESP32) management and data query endpoints."""

import csv
import io
import re
import uuid
import zipfile
import zoneinfo
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_role
from app.config import settings
from app.database import get_db
from app.gateway_auth import get_current_gateway
from app.models.master import Gateway, Sensor, Zone
from app.models.timeseries import SensorReading
from app.models.user import Role, User
from app.rate_limit import limiter
from app.schemas.gateway import PairingCodeRequest, PairingCodeResponse
from app.schemas.sensor import (
    ClaimSensorRequest,
    ClaimSensorResponse,
    DataPoint,
    MoveSensorRequest,
    SensorDataResponse,
    SensorResponse,
    SensorUpdateRequest,
)
from app.services.gateway_service import gateway_commands_cache, generate_pairing_code

router = APIRouter(prefix="/sensors", tags=["sensors"])
_tenant_manager = require_role([Role.OWNER, Role.ADMIN])

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
    zone_id: uuid.UUID | None = Query(None),
    gateway_id: uuid.UUID | None = Query(None),
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
        last_seen = sensor.last_seen
        if last_seen and last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=UTC)
        is_online = bool(last_seen and (now - last_seen) < LIVENESS_THRESHOLD)
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
                sms_alerts_enabled=sensor.sms_alerts_enabled,
            )
        )
    return results


# ── Claim Sensor (Gateway → Cloud) ─────────────────


@router.post("/claim", response_model=ClaimSensorResponse, status_code=201)
async def claim_sensor(
    data: ClaimSensorRequest,
    gateway: Gateway = Depends(get_current_gateway),
    db: Session = Depends(get_db),
):
    """Gateway claims a discovered ESP32 sensor by MAC address."""
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
    current_user: User = Depends(_tenant_manager),
    db: Session = Depends(get_db),
):
    """Generate a short-lived pairing code for a sensor to use in its Captive Portal."""
    return generate_pairing_code(db, current_user, data.zone_id)


# ── Move / Update Sensor ─────────────────────────────────────


@router.patch("/{sensor_id}", response_model=SensorResponse)
async def update_sensor(
    sensor_id: uuid.UUID,
    data: SensorUpdateRequest,
    current_user: User = Depends(_tenant_manager),
    db: Session = Depends(get_db),
):
    """Update sensor settings (name, sms_alerts_enabled)."""
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

    sensor, gw = result

    if data.name is not None:
        sensor.name = data.name
    if data.sms_alerts_enabled is not None:
        sensor.sms_alerts_enabled = data.sms_alerts_enabled

    db.commit()
    db.refresh(sensor)

    now = datetime.now(UTC)
    last_seen = (
        sensor.last_seen.replace(tzinfo=UTC)
        if sensor.last_seen and sensor.last_seen.tzinfo is None
        else sensor.last_seen
    )
    is_online = bool(last_seen and (now - last_seen) < LIVENESS_THRESHOLD)

    return SensorResponse(
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
        sms_alerts_enabled=sensor.sms_alerts_enabled,
    )


@router.patch("/{sensor_id}/move", response_model=SensorResponse)
async def move_sensor(
    sensor_id: uuid.UUID,
    data: MoveSensorRequest,
    current_user: User = Depends(_tenant_manager),
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
        sms_alerts_enabled=sensor.sms_alerts_enabled,
    )


# ── Delete Sensor ───────────────────────────────────


@router.delete("/{sensor_id}", status_code=204)
async def delete_sensor(
    sensor_id: uuid.UUID,
    current_user: User = Depends(_tenant_manager),
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
    sensor_id: uuid.UUID,
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
            # Safe use of f-string: bucket_size is mapped from enum/dict, not user input
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


class _ExportBudget:
    """Bound uncompressed export bytes across every ZIP member."""

    def __init__(self, limit: int):
        self.limit = limit
        self.used = 0

    def consume(self, size: int) -> None:
        self.used += size
        if self.used > self.limit:
            raise HTTPException(status_code=413, detail="Sensor export exceeds byte limit")


class _BoundedZipTextWriter:
    """Minimal text writer for csv.writer that writes directly into a ZIP member."""

    def __init__(self, destination, budget: _ExportBudget):
        self.destination = destination
        self.budget = budget

    def write(self, value: str) -> int:
        encoded = value.encode("utf-8")
        self.budget.consume(len(encoded))
        self.destination.write(encoded)
        return len(value)


def _safe_export_component(value: str | None, fallback: str) -> str:
    component = re.sub(r"[^A-Za-z0-9_.-]+", "_", value or "").strip("._-")
    component = re.sub(r"\.{2,}", ".", component)
    return (component[:80] or fallback).strip("._-") or fallback


def _safe_export_text(value: object) -> str:
    text_value = str(value).replace("\r", " ").replace("\n", " ").strip()
    # Prevent spreadsheet formula execution when a CSV is opened interactively.
    if text_value.startswith(("=", "+", "-", "@")):
        return f"'{text_value}"
    return text_value


@router.get("/{sensor_id}/export")
@limiter.limit("3/minute")
async def export_sensor_data(
    request: Request,
    sensor_id: uuid.UUID,
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

    reading_filter = (
        SensorReading.sensor_id == sensor_id,
        SensorReading.timestamp >= cutoff,
    )
    row_count = (
        db.query(func.count()).select_from(SensorReading).filter(*reading_filter).scalar() or 0
    )
    if row_count > settings.sensor_export_max_rows:
        raise HTTPException(
            status_code=413,
            detail=f"Sensor export exceeds {settings.sensor_export_max_rows} row limit",
        )

    # Get distinct kinds
    kinds = (
        db.query(SensorReading.kind)
        .filter(*reading_filter)
        .distinct()
        .order_by(SensorReading.kind.asc())
        .limit(settings.sensor_export_max_kinds + 1)
        .all()
    )

    if not kinds:
        raise HTTPException(status_code=404, detail="No data available for export")
    if len(kinds) > settings.sensor_export_max_kinds:
        raise HTTPException(
            status_code=413,
            detail=f"Sensor export exceeds {settings.sensor_export_max_kinds} kind limit",
        )

    # Build zone metadata header
    zone_meta = (
        f"# Zone: {_safe_export_text(zone.name)}\n"
        f"# Type: {_safe_export_text(zone.zone_type)}\n"
        f"# Location: {_safe_export_text(zone.location or '—')}\n"
    )
    if zone.latitude is not None and zone.longitude is not None:
        zone_meta += f"# GPS: {zone.latitude}, {zone.longitude}\n"
    zone_meta += f"# Gateway: {_safe_export_text(gw.name or gw.hardware_id)}\n"
    zone_meta += f"# Sensor: {_safe_export_text(sensor.name or sensor.mac_address)}\n"

    # Build a strictly bounded ZIP. Each CSV is written directly into its ZIP
    # member so there is no second, unbounded StringIO copy per measurement kind.
    zip_buffer = io.BytesIO()
    budget = _ExportBudget(settings.sensor_export_max_bytes)
    emitted_rows = 0
    switzerland_tz = zoneinfo.ZoneInfo("Europe/Zurich")

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for index, (kind,) in enumerate(kinds, start=1):
            safe_kind = _safe_export_component(kind, "reading")
            member_name = f"{index:02d}_{safe_kind}.csv"

            readings = (
                db.query(
                    SensorReading.timestamp,
                    SensorReading.value,
                    SensorReading.unit,
                )
                .filter(
                    SensorReading.sensor_id == sensor_id,
                    SensorReading.kind == kind,
                    SensorReading.timestamp >= cutoff,
                )
                .order_by(SensorReading.timestamp.asc())
                .yield_per(2_000)
            )

            with zf.open(member_name, "w") as member:
                text_writer = _BoundedZipTextWriter(member, budget)
                text_writer.write(zone_meta)
                csv_writer = csv.writer(text_writer, lineterminator="\n")
                csv_writer.writerow(["timestamp", "value", "unit"])

                for ts, val, unit in readings:
                    emitted_rows += 1
                    if emitted_rows > settings.sensor_export_max_rows:
                        raise HTTPException(
                            status_code=413, detail="Sensor export exceeds row limit"
                        )
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=UTC)
                    ts_local = ts.astimezone(switzerland_tz)
                    csv_writer.writerow(
                        [
                            ts_local.strftime("%Y-%m-%d %H:%M:%S"),
                            round(val, 4),
                            _safe_export_text(unit),
                        ]
                    )

    archive_size = zip_buffer.getbuffer().nbytes
    if archive_size > settings.sensor_export_max_bytes:
        raise HTTPException(status_code=413, detail="Sensor export exceeds byte limit")

    zip_buffer.seek(0)
    sensor_label = _safe_export_component(sensor.name or sensor.mac_address, "sensor")
    filename = f"greenmind_{sensor_label}_{range}_{datetime.now(UTC).strftime('%Y%m%d')}.zip"

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(archive_size),
        },
    )
