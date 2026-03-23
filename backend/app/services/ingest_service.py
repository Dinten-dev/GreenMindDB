"""Service layer for handling complex ingestion logic with idempotency."""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.ingest_log import IngestLog
from app.models.master import Device, Sensor
from app.models.timeseries import SensorReading
from app.schemas.ingest import IngestRequest


class DuplicateIngestionError(Exception):
    pass


def process_ingestion(data: IngestRequest, device: Device, db: Session) -> int:
    """
    Store IoT data, applying idempotency checks.
    Returns the number of ingested sensor readings.
    """
    # 1. Idempotency Check
    existing_log = (
        db.query(IngestLog).filter(IngestLog.measurement_id == data.measurement_id).first()
    )
    if existing_log and existing_log.status == "success":
        raise DuplicateIngestionError(f"Measurement {data.measurement_id} already ingested")

    # 2. Log Start (could trace 'processing' state or just write 'success' later)
    log = existing_log or IngestLog(
        measurement_id=data.measurement_id,
        device_id=device.id,
    )
    db.add(log)

    now = datetime.now(UTC)
    ingested_count = 0

    # 3. Store Readings
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
            db.flush()  # We need the sensor.id

        # Process timestamp
        ts = now
        if reading.timestamp:
            try:
                ts = datetime.fromisoformat(reading.timestamp.replace("Z", "+00:00"))
            except ValueError:
                ts = now

        # Create timeseries row
        sr = SensorReading(
            timestamp=ts,
            sensor_id=sensor.id,
            value=reading.value,
            unit=reading.unit,
            measurement_id=data.measurement_id,
        )
        db.add(sr)
        ingested_count += 1

    # 4. Update Device Status
    device.last_seen = now
    device.status = "online"

    # 5. Commit
    log.status = "success"
    log.raw_file_reference = data.raw_file_reference
    db.commit()

    return ingested_count
