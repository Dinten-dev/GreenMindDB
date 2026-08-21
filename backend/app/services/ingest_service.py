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


_last_alert_times = {}
ALERT_COOLDOWN_MINUTES = 720


def process_ingestion(data: IngestRequest, gateway: Gateway, db: Session) -> tuple[int, list[dict]]:
    """
    Store IoT data, applying idempotency checks.
    Returns a tuple of (ingested_count, list_of_alerts_to_trigger).
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
    alerts_to_trigger = []

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

        # Check for electrode alert condition
        if reading.sensor_kind in ("bio_signal", "bioelectric") and getattr(
            sensor, "sms_alerts_enabled", True
        ):
            is_flatline = reading.value <= 10.0
            is_saturated = reading.value >= 3200.0
            if is_flatline or is_saturated:
                last_alert = _last_alert_times.get(reading.sensor_mac)
                if (
                    not last_alert
                    or (now - last_alert).total_seconds() > ALERT_COOLDOWN_MINUTES * 60
                ):
                    _last_alert_times[reading.sensor_mac] = now
                    from app.models.user import User

                    if sensor.gateway and sensor.gateway.zone:
                        zone = sensor.gateway.zone
                        users = (
                            db.query(User)
                            .filter(
                                User.organization_id == zone.organization_id,
                                User.phone_number.isnot(None),
                                User.phone_number != "",
                            )
                            .all()
                        )
                        for u in users:
                            alerts_to_trigger.append(
                                {
                                    "phone_number": u.phone_number,
                                    "sensor_mac": reading.sensor_mac,
                                    "zone_name": zone.name,
                                }
                            )

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

    return ingested_count, alerts_to_trigger
