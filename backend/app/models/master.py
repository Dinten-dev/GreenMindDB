"""Master-data models: Zone, Gateway, Sensor."""

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base

ZONE_TYPES = ("GREENHOUSE", "OPEN_FIELD", "VERTICAL_FARM", "ORCHARD")


class Zone(Base):
    __tablename__ = "zone"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(200), nullable=False)
    location = Column(String(500), nullable=True)
    zone_type = Column(String(20), nullable=False, default="GREENHOUSE")
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    organization = relationship("Organization", back_populates="zones")
    gateways = relationship("Gateway", back_populates="zone", cascade="all, delete-orphan")


class Gateway(Base):
    """Raspberry Pi gateway that bridges ESP32 sensors to the cloud."""

    __tablename__ = "gateway"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    zone_id = Column(
        UUID(as_uuid=True),
        ForeignKey("zone.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    hardware_id = Column(String(100), nullable=False, unique=True)
    name = Column(String(200), nullable=True)
    description = Column(Text, nullable=True)
    local_ip = Column(String(45), nullable=True)
    fw_version = Column(String(50), nullable=True)
    status = Column(String(20), nullable=False, default="offline")
    api_key_hash = Column(String(200), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    last_seen = Column(DateTime(timezone=True), nullable=True)
    paired_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    # Remote management columns (added in migration 0013)
    app_version = Column(String(50), nullable=True)
    config_version = Column(String(50), nullable=True)
    agent_version = Column(String(50), nullable=True)
    rollout_ring = Column(String(50), nullable=True, default="stable")
    maintenance_mode = Column(Boolean, nullable=True, default=False)
    blocked = Column(Boolean, nullable=True, default=False)
    os_version = Column(String(100), nullable=True)
    disk_free_mb = Column(Integer, nullable=True)
    update_download_status = Column(String(20), nullable=True)
    update_apply_status = Column(String(20), nullable=True)
    signature_status = Column(String(20), nullable=True)

    zone = relationship("Zone", back_populates="gateways")
    sensors = relationship("Sensor", back_populates="gateway", cascade="all, delete-orphan")


class Sensor(Base):
    """Physical ESP32 sensor module claimed by a gateway."""

    __tablename__ = "sensor"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    gateway_id = Column(
        UUID(as_uuid=True),
        ForeignKey("gateway.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mac_address = Column(String(17), nullable=False, unique=True)
    name = Column(String(200), nullable=True)
    sensor_type = Column(String(50), nullable=False, default="generic")
    status = Column(String(20), nullable=False, default="offline")
    last_seen = Column(DateTime(timezone=True), nullable=True)
    claimed_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    gateway = relationship("Gateway", back_populates="sensors")
