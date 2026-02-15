"""Master-data models: Greenhouse, Zone, Plant, Device, Sensor."""
import uuid

from sqlalchemy import (
    Column, DateTime, ForeignKey, String, Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.database import Base


class Greenhouse(Base):
    __tablename__ = "greenhouse"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    location = Column(String(500), nullable=True)
    timezone = Column(String(50), nullable=False, default="UTC")

    zones = relationship("Zone", back_populates="greenhouse", cascade="all, delete-orphan")
    devices = relationship("Device", back_populates="greenhouse", cascade="all, delete-orphan")
    users = relationship("User", back_populates="greenhouse")


class Zone(Base):
    __tablename__ = "zone"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    greenhouse_id = Column(UUID(as_uuid=True), ForeignKey("greenhouse.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(200), nullable=False)

    greenhouse = relationship("Greenhouse", back_populates="zones")
    plants = relationship("Plant", back_populates="zone", cascade="all, delete-orphan")


class Plant(Base):
    __tablename__ = "plant"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    zone_id = Column(UUID(as_uuid=True), ForeignKey("zone.id", ondelete="CASCADE"), nullable=False, index=True)
    species = Column(String(200), nullable=False)
    cultivar = Column(String(200), nullable=True)
    planted_at = Column(DateTime(timezone=True), nullable=True)
    tags = Column(JSONB, server_default=text("'{}'::jsonb"))

    zone = relationship("Zone", back_populates="plants")


class Device(Base):
    __tablename__ = "device"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    greenhouse_id = Column(UUID(as_uuid=True), ForeignKey("greenhouse.id", ondelete="CASCADE"), nullable=False, index=True)
    serial = Column(String(100), nullable=False, unique=True)
    type = Column(String(50), nullable=False)
    fw_version = Column(String(50), nullable=True)
    last_seen = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(20), nullable=False, default="offline")

    greenhouse = relationship("Greenhouse", back_populates="devices")
    sensors = relationship("Sensor", back_populates="device", cascade="all, delete-orphan")


class Sensor(Base):
    __tablename__ = "sensor"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id = Column(UUID(as_uuid=True), ForeignKey("device.id", ondelete="CASCADE"), nullable=False, index=True)
    plant_id = Column(UUID(as_uuid=True), ForeignKey("plant.id", ondelete="SET NULL"), nullable=True, index=True)
    zone_id = Column(UUID(as_uuid=True), ForeignKey("zone.id", ondelete="SET NULL"), nullable=True, index=True)
    kind = Column(String(100), nullable=False)
    unit = Column(String(20), nullable=False)
    calibration = Column(JSONB, server_default=text("'{}'::jsonb"))

    device = relationship("Device", back_populates="sensors")
