"""Master-data models: Greenhouse, Gateway, Sensor."""

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class Greenhouse(Base):
    __tablename__ = "greenhouse"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(200), nullable=False)
    location = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    organization = relationship("Organization", back_populates="greenhouses")
    gateways = relationship("Gateway", back_populates="greenhouse", cascade="all, delete-orphan")


class Gateway(Base):
    """Raspberry Pi gateway that bridges ESP32 sensors to the cloud."""

    __tablename__ = "gateway"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    greenhouse_id = Column(
        UUID(as_uuid=True),
        ForeignKey("greenhouse.id", ondelete="CASCADE"),
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

    greenhouse = relationship("Greenhouse", back_populates="gateways")
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
