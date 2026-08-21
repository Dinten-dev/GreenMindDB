"""Service layer for handling complex ingestion logic with idempotency."""

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.ingest_log import IngestLog
from app.models.master import Gateway, Sensor
from app.models.timeseries import SensorReading
from app.schemas.ingest import IngestRequest


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
        if existing_log.gateway_id != gateway.id:
            raise HTTPException(status_code=409, detail="Measurement ID belongs to another gateway")
        raise DuplicateIngestionError(f"Measurement {data.measurement_id} already ingested")

    requested_macs = {reading.sensor_mac for reading in data.readings}
    sensors = (
        db.query(Sensor).filter(Sensor.mac_address.in_(requested_macs)).all()
        if requested_macs
        else []
    )
    sensors_by_mac = {sensor.mac_address: sensor for sensor in sensors}
    if any(
        mac not in sensors_by_mac or sensors_by_mac[mac].gateway_id != gateway.id
        for mac in requested_macs
    ):
        raise HTTPException(
            status_code=403,
            detail="Every sensor must already be registered to the authenticated gateway",
        )

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
        sensor = sensors_by_mac[reading.sensor_mac]

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
