from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Float, SmallInteger, text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
from app.database import Base

class Device(Base):
    """IoT Device registry."""
    __tablename__ = "device"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    device_type = Column(String(50), nullable=False) # esp32, raspi, etc.
    description = Column(String(255), nullable=True)
    api_key_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"))

    # Relationships
    channels = relationship("TelemetryChannel", back_populates="device")


class TelemetryChannel(Base):
    """Mapping of a sensor channel to a species and metric."""
    __tablename__ = "telemetry_channel"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    species_id = Column(Integer, ForeignKey("species.id"), nullable=False, index=True)
    metric_id = Column(Integer, ForeignKey("metric.id"), nullable=False)
    device_id = Column(UUID(as_uuid=True), ForeignKey("device.id"), nullable=False)
    channel_key = Column(String(100), nullable=False) # No longer unique globally, unique per device/species/metric logical combo
    unit = Column(String(20), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"))

    __table_args__ = (
        UniqueConstraint('species_id', 'metric_id', name='uq_telemetry_channel_species_metric'),
    )

    # Relationships
    device = relationship("Device", back_populates="channels")
    species = relationship("Species")
    metric = relationship("Metric")


class TelemetryMeasurement(Base):
    """Timeseries data (Hypertable)."""
    __tablename__ = "telemetry_measurement"

    time = Column(TIMESTAMP(timezone=True), primary_key=True, nullable=False, index=True)
    channel_id = Column(UUID(as_uuid=True), ForeignKey("telemetry_channel.id"), primary_key=True, nullable=False, index=True)
    value = Column(Float, nullable=False)
    quality = Column(SmallInteger, default=0) # 0=ok, 1=artefact, 2=missing

    # No relationships defined for high-throughput table usually, 
    # but we could define if needed. For write speed, often kept minimal.
