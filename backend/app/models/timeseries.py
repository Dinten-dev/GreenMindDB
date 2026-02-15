"""Timeseries models for TimescaleDB hypertables."""
from sqlalchemy import (
    Column, Float, ForeignKey, Integer, SmallInteger, text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID

from app.database import Base


class PlantSignal1Hz(Base):
    """1 Hz bioelectric plant signal – TimescaleDB hypertable."""
    __tablename__ = "plant_signal_1hz"

    time = Column(TIMESTAMP(timezone=True), primary_key=True, nullable=False)
    greenhouse_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    plant_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    sensor_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    value_uv = Column(Float, nullable=False)
    quality = Column(SmallInteger, default=0)
    meta = Column(JSONB, server_default=text("'{}'::jsonb"))

    __table_args__ = (
        {"timescaledb_hypertable": {"time_column_name": "time"}},
    )


class EnvMeasurement(Base):
    """Environmental sensor measurement – TimescaleDB hypertable."""
    __tablename__ = "env_measurement"

    time = Column(TIMESTAMP(timezone=True), primary_key=True, nullable=False)
    greenhouse_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    sensor_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    value = Column(Float, nullable=False)
    quality = Column(SmallInteger, default=0)
    meta = Column(JSONB, server_default=text("'{}'::jsonb"))

    __table_args__ = (
        {"timescaledb_hypertable": {"time_column_name": "time"}},
    )
