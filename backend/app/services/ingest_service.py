"""Service layer for handling complex ingestion logic with idempotency."""

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models.ingest_log import IngestLog
from app.models.master import Gateway, Sensor
from app.models.timeseries import SensorReading
from app.schemas.ingest import IngestRequest


class DuplicateIngestionError(Exception):
    pass


_last_alert_times = {}
ALERT_COOLDOWN_MINUTES = 720


def _insert_readings_idempotently(db: Session, rows: list[dict]) -> int:
    """Insert readings once, even when legacy gateways change retry IDs."""
    if not rows:
        return 0
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgresql_insert(SensorReading).values(rows)
    elif dialect == "sqlite":
        statement = sqlite_insert(SensorReading).values(rows)
    else:
        raise RuntimeError(f"Unsupported ingestion database dialect: {dialect}")
    statement = statement.on_conflict_do_nothing(index_elements=["timestamp", "sensor_id", "kind"])
    result = db.execute(statement)
    return max(0, result.rowcount or 0)


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
    reading_rows = []
    alerts_to_trigger = []

    # 3. Store Readings
    for reading in data.readings:
        sensor = sensors_by_mac[reading.sensor_mac]

        # Use pre-validated timestamp from schema or fall back to now
        ts = reading.timestamp or now

        reading_rows.append(
            {
                "timestamp": ts,
                "sensor_id": sensor.id,
                "kind": reading.sensor_kind,
                "value": reading.value,
                "unit": reading.unit,
                "measurement_id": data.measurement_id,
                "source_sequence": reading.source_sequence,
                "source_uptime_ms": reading.source_uptime_ms,
                "source_dropped_samples_total": reading.source_dropped_samples_total,
                "sample_count": reading.sample_count,
                "sample_rate_hz": reading.sample_rate_hz,
                "median": reading.median,
                "rms": reading.rms,
                "standard_deviation": reading.standard_deviation,
                "minimum": reading.minimum,
                "maximum": reading.maximum,
                "p05": reading.p05,
                "p95": reading.p95,
                "coverage_ratio": reading.coverage_ratio,
                "source_boot_id": reading.source_boot_id,
                "protocol_version": reading.protocol_version,
                "firmware_version": reading.firmware_version,
                "calibration_version": reading.calibration_version,
                "quality_valid_count": reading.quality_valid_count,
                "quality_lead_off_count": reading.quality_lead_off_count,
                "quality_rail_high_count": reading.quality_rail_high_count,
                "quality_rail_low_count": reading.quality_rail_low_count,
                "quality_jump_count": reading.quality_jump_count,
                "quality_recovery_count": reading.quality_recovery_count,
            }
        )

        # Check for electrode alert condition
        if reading.sensor_kind in ("bio_signal", "bioelectric") and getattr(
            sensor, "sms_alerts_enabled", True
        ):
            is_flatline = reading.value <= 10.0
            is_saturated = reading.value >= 3200.0 or bool(
                (reading.quality_rail_high_count or 0) + (reading.quality_rail_low_count or 0)
            )
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
    ingested_count = _insert_readings_idempotently(db, reading_rows)
    log.status = "success"
    log.raw_file_reference = data.raw_file_reference
    db.commit()

    return ingested_count, alerts_to_trigger
