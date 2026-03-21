"""Master-data models: Greenhouse, Device, Sensor."""

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
    devices = relationship("Device", back_populates="greenhouse", cascade="all, delete-orphan")


class Device(Base):
    __tablename__ = "device"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    greenhouse_id = Column(
        UUID(as_uuid=True),
        ForeignKey("greenhouse.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    serial = Column(String(100), nullable=False, unique=True)
    name = Column(String(200), nullable=True)
    description = Column(Text, nullable=True)
    type = Column(String(50), nullable=False, default="esp32")
    fw_version = Column(String(50), nullable=True)
    last_seen = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(20), nullable=False, default="offline")
    api_key_hash = Column(String(200), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    paired_at = Column(DateTime(timezone=True), nullable=True)

    greenhouse = relationship("Greenhouse", back_populates="devices")
    sensors = relationship("Sensor", back_populates="device", cascade="all, delete-orphan")


class Sensor(Base):
    __tablename__ = "sensor"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id = Column(
        UUID(as_uuid=True), ForeignKey("device.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind = Column(String(100), nullable=False)
    unit = Column(String(20), nullable=False)
    label = Column(String(200), nullable=True)

    device = relationship("Device", back_populates="sensors")
