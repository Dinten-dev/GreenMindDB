"""Timeseries model for TimescaleDB hypertable."""

from sqlalchemy import Column, Float, String
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
