"""IoT data ingestion endpoint – gateways push sensor readings here."""

from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.auth import verify_password
from app.database import get_db
from app.models.master import Gateway, Sensor
from app.routers.ws import manager
from app.schemas.ingest import IngestRequest, IngestResponse
from app.services.ingest_service import DuplicateIngestionError, process_ingestion
from app.services.notification_service import notification_service

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("", response_model=IngestResponse, status_code=201)
async def ingest_data(
    data: IngestRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    x_api_key: str | None = Header(None),
):
    """
    Ingest sensor readings from a gateway.
    Authenticate via X-Api-Key header (the key returned from gateway registration).
    """
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-Api-Key header")

    gateway = db.query(Gateway).filter(Gateway.hardware_id == data.gateway_serial).first()
    if not gateway:
        raise HTTPException(
            status_code=404, detail="Gateway not found"
        )
    if not gateway.is_active:
        raise HTTPException(status_code=403, detail="Gateway is deactivated")
    if not gateway.api_key_hash:
        raise HTTPException(status_code=403, detail="Gateway has no API key")

    # Verify API key
    if not verify_password(x_api_key, gateway.api_key_hash):
        raise HTTPException(status_code=401, detail="Invalid API key")

    try:
        ingested, alerts = process_ingestion(data, gateway, db)
        for alert in alerts:
            background_tasks.add_task(
                notification_service.send_electrode_disconnect_alert,
                phone_number=alert["phone_number"],
                sensor_mac=alert["sensor_mac"],
                zone_name=alert["zone_name"]
            )
    except DuplicateIngestionError:
        return IngestResponse(
            status="duplicate",
            ingested=0,
            gateway_id=str(gateway.id),
            measurement_id=data.measurement_id,
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
                "timestamp": r.timestamp or now.isoformat(),
            }
            for r in data.readings
        ]
        await manager.broadcast_to_zone(
            {
                "event": "new_readings",
                "gateway_id": str(gateway.id),
                "measurement_id": data.measurement_id,
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

        sensor = db.query(Sensor).filter(Sensor.mac_address == r.sensor_mac).first()
        if not sensor:
            continue

        sensor_readings = [
            {
                "value": rd.value,
                "unit": rd.unit,
                "kind": rd.sensor_kind,
                "timestamp": rd.timestamp or now.isoformat(),
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
        measurement_id=data.measurement_id,
    )
