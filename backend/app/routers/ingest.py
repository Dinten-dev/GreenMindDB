"""IoT data ingestion endpoint – gateways push sensor readings here."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.gateway_auth import get_current_gateway
from app.models.master import Gateway, Sensor
from app.routers.ws import manager
from app.schemas.ingest import IngestRequest, IngestResponse
from app.services.ingest_service import DuplicateIngestionError, process_ingestion

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("", response_model=IngestResponse, status_code=201)
async def ingest_data(
    data: IngestRequest,
    gateway: Gateway = Depends(get_current_gateway),
    db: Session = Depends(get_db),
):
    """
    Ingest sensor readings from a gateway.
    Authenticate via X-Api-Key header (the key returned from gateway registration).
    """
    if gateway.hardware_id != data.gateway_serial:
        raise HTTPException(status_code=403, detail="Gateway identity mismatch")

    try:
        ingested = process_ingestion(data, gateway, db)
    except DuplicateIngestionError:
        return IngestResponse(
            status="duplicate",
            ingested=0,
            gateway_id=str(gateway.id),
            measurement_id=str(data.measurement_id),
        )

    # Broadcast real-time update to zone subscribers
    if gateway.zone_id:
        now = datetime.now(UTC)
        readings_out = [
            {
                "sensor_mac": r.sensor_mac,
                "sensor_kind": r.sensor_kind,
                "value": r.value,
                "unit": r.unit,
                "timestamp": r.timestamp.isoformat() if r.timestamp else now.isoformat(),
            }
            for r in data.readings
        ]
        await manager.broadcast_to_zone(
            {
                "event": "new_readings",
                "gateway_id": str(gateway.id),
                "measurement_id": str(data.measurement_id),
                "readings": readings_out,
            },
            str(gateway.zone_id),
        )

    # Broadcast to per-sensor WebSocket subscribers (live view)
    now = datetime.now(UTC)
    sensor_macs_seen: set[str] = set()
    for r in data.readings:
        if r.sensor_mac in sensor_macs_seen:
            continue
        sensor_macs_seen.add(r.sensor_mac)

        sensor = (
            db.query(Sensor)
            .filter(Sensor.mac_address == r.sensor_mac, Sensor.gateway_id == gateway.id)
            .first()
        )
        if not sensor:
            continue

        sensor_readings = [
            {
                "value": rd.value,
                "unit": rd.unit,
                "kind": rd.sensor_kind,
                "timestamp": rd.timestamp.isoformat() if rd.timestamp else now.isoformat(),
            }
            for rd in data.readings
            if rd.sensor_mac == r.sensor_mac
        ]
        await manager.broadcast_to_sensor(
            {
                "event": "live_reading",
                "sensor_id": str(sensor.id),
                "sensor_mac": r.sensor_mac,
                "readings": sensor_readings,
            },
            str(sensor.id),
        )

    return IngestResponse(
        status="success",
        ingested=ingested,
        gateway_id=str(gateway.id),
        measurement_id=str(data.measurement_id),
    )
