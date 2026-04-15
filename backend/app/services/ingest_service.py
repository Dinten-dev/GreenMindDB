"""Service layer for handling complex ingestion logic with idempotency."""

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.ingest_log import IngestLog
from app.models.master import Gateway, Sensor
from app.models.timeseries import SensorReading
from app.schemas.ingest import IngestRequest

logger = logging.getLogger(__name__)


class DuplicateIngestionError(Exception):
    pass


def process_ingestion(data: IngestRequest, gateway: Gateway, db: Session) -> int:
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

    # 2. Log Start
    log = existing_log or IngestLog(
        measurement_id=data.measurement_id,
        gateway_id=gateway.id,
    )
    db.add(log)

    now = datetime.now(UTC)
    ingested_count = 0

    # 3. Store Readings
    for reading in data.readings:
        # Find sensor by MAC
        sensor = db.query(Sensor).filter(Sensor.mac_address == reading.sensor_mac).first()

        if sensor and sensor.gateway_id != gateway.id:
            # Sensor belongs to a different gateway – reject this reading
            logger.warning(
                "Sensor-gateway affinity violation: MAC=%s belongs to gateway=%s but request from gateway=%s",
                reading.sensor_mac,
                sensor.gateway_id,
                gateway.id,
            )
            continue

        if not sensor:
            # Auto-create sensor under this gateway
            sensor = Sensor(
                gateway_id=gateway.id,
                mac_address=reading.sensor_mac,
                name=f"{gateway.name} – {reading.sensor_mac}",
                sensor_type="auto",
                status="online",
                last_seen=now,
            )
            db.add(sensor)
            db.flush()

        # Use pre-validated timestamp from schema or fall back to now
        ts = reading.timestamp or now

        # Create timeseries row
        sr = SensorReading(
            timestamp=ts,
            sensor_id=sensor.id,
            kind=reading.sensor_kind,
            value=reading.value,
            unit=reading.unit,
            measurement_id=data.measurement_id,
        )
        db.add(sr)
        ingested_count += 1

        # Update sensor last_seen
        sensor.last_seen = now
        sensor.status = "online"

    # 4. Update Gateway Status
    gateway.last_seen = now
    gateway.status = "online"

    # 5. Commit
    log.status = "success"
    log.raw_file_reference = data.raw_file_reference
    db.commit()

    return ingested_count
