"""Timeseries model for TimescaleDB hypertable."""

from sqlalchemy import BigInteger, Column, Float, Integer, String
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID

from app.database import Base


class SensorReading(Base):
    """Sensor reading – TimescaleDB hypertable."""

    __tablename__ = "sensor_reading"

    timestamp = Column(TIMESTAMP(timezone=True), primary_key=True, nullable=False)
    sensor_id = Column(UUID(as_uuid=True), primary_key=True, nullable=False, index=True)
    kind = Column(String(100), primary_key=True, nullable=False)
    value = Column(Float, nullable=False)
    unit = Column(String(20), nullable=False)
    measurement_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    source_sequence = Column(BigInteger, nullable=True)
    source_uptime_ms = Column(BigInteger, nullable=True)
    source_dropped_samples_total = Column(BigInteger, nullable=True)
    sample_count = Column(Integer, nullable=True)
    sample_rate_hz = Column(Float, nullable=True)
    median = Column(Float, nullable=True)
    rms = Column(Float, nullable=True)
    standard_deviation = Column(Float, nullable=True)
    minimum = Column(Float, nullable=True)
    maximum = Column(Float, nullable=True)
    p05 = Column(Float, nullable=True)
    p95 = Column(Float, nullable=True)
    coverage_ratio = Column(Float, nullable=True)
    source_boot_id = Column(BigInteger, nullable=True)
    protocol_version = Column(Integer, nullable=True)
    firmware_version = Column(String(50), nullable=True)
    calibration_version = Column(String(50), nullable=True)
    quality_valid_count = Column(Integer, nullable=True)
    quality_lead_off_count = Column(Integer, nullable=True)
    quality_rail_high_count = Column(Integer, nullable=True)
    quality_rail_low_count = Column(Integer, nullable=True)
    quality_jump_count = Column(Integer, nullable=True)
    quality_recovery_count = Column(Integer, nullable=True)
