"""IoT data ingestion endpoint – devices push sensor readings here."""
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.database import get_db
from app.models.master import Device, Sensor
from app.models.timeseries import SensorReading
from app.auth import verify_password

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


class ReadingPayload(BaseModel):
    sensor_kind: str
    value: float
    unit: str
    timestamp: Optional[str] = None


class IngestRequest(BaseModel):
    device_serial: str
    readings: List[ReadingPayload]


class IngestResponse(BaseModel):
    ingested: int
    device_id: str


@router.post("", response_model=IngestResponse, status_code=201)
async def ingest_data(
    data: IngestRequest,
    db: Session = Depends(get_db),
    x_api_key: Optional[str] = Header(None),
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

    now = datetime.now(timezone.utc)
    ingested = 0

    for reading in data.readings:
        # Find or create sensor
        sensor = (
            db.query(Sensor)
            .filter(Sensor.device_id == device.id, Sensor.kind == reading.sensor_kind)
            .first()
        )
        if not sensor:
            sensor = Sensor(
                device_id=device.id,
                kind=reading.sensor_kind,
                unit=reading.unit,
                label=f"{device.name} – {reading.sensor_kind}",
            )
            db.add(sensor)
            db.flush()

        ts = now
        if reading.timestamp:
            try:
                ts = datetime.fromisoformat(reading.timestamp.replace("Z", "+00:00"))
            except ValueError:
                ts = now

        sr = SensorReading(
            timestamp=ts,
            sensor_id=sensor.id,
            value=reading.value,
            unit=reading.unit,
        )
        db.add(sr)
        ingested += 1

    # Update device last_seen and status
    device.last_seen = now
    device.status = "online"
    db.commit()

    return IngestResponse(ingested=ingested, device_id=str(device.id))
