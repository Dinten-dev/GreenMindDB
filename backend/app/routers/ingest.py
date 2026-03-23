"""IoT data ingestion endpoint – devices push sensor readings here."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.auth import verify_password
from app.database import get_db
from app.models.master import Device
from app.routers.ws import manager
from app.schemas.ingest import IngestRequest, IngestResponse
from app.services.ingest_service import DuplicateIngestionError, process_ingestion

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("", response_model=IngestResponse, status_code=201)
async def ingest_data(
    data: IngestRequest,
    db: Session = Depends(get_db),
    x_api_key: str | None = Header(None),
):
    """
    Ingest sensor readings from a device.
    Authenticate via X-Api-Key header (the key returned from device pairing).
    """
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-Api-Key header")

    # Find device by serial
    device = db.query(Device).filter(Device.serial == data.device_serial).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    if not device.is_active:
        raise HTTPException(status_code=403, detail="Device is deactivated")
    if not device.api_key_hash:
        raise HTTPException(status_code=403, detail="Device has no API key")

    # Verify API key
    if not verify_password(x_api_key, device.api_key_hash):
        raise HTTPException(status_code=401, detail="Invalid API key")

    try:
        ingested = process_ingestion(data, device, db)
    except DuplicateIngestionError:
        return IngestResponse(
            status="duplicate",
            ingested=0,
            device_id=str(device.id),
            measurement_id=data.measurement_id,
        )

    # Broadcast real-time update if assigned to greenhouse
    if device.greenhouse_id:
        now = datetime.now(UTC)
        readings_out = [
            {
                "sensor_kind": r.sensor_kind,
                "value": r.value,
                "unit": r.unit,
                "timestamp": r.timestamp or now.isoformat(),
            }
            for r in data.readings
        ]
        await manager.broadcast_to_greenhouse(
            {
                "event": "new_readings",
                "device_id": str(device.id),
                "measurement_id": data.measurement_id,
                "readings": readings_out,
            },
            str(device.greenhouse_id),
        )

    return IngestResponse(
        status="success",
        ingested=ingested,
        device_id=str(device.id),
        measurement_id=data.measurement_id,
    )
