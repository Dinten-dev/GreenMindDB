"""Plant and sensor assignment models."""

import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class Plant(Base):
    __tablename__ = "plant"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="CASCADE"), nullable=False, index=True
    )
    zone_id = Column(
        UUID(as_uuid=True), ForeignKey("zone.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name = Column(String(200), nullable=False)
    plant_code = Column(String(100), nullable=True)
    species = Column(String(200), nullable=True)
    cultivar = Column(String(200), nullable=True)
    description = Column(Text, nullable=True)
    planted_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(50), nullable=False, default="active")  # active, archived, removed
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=text("now()"), nullable=False
    )

    organization = relationship("Organization")
    zone = relationship("Zone")
    sensor_assignments = relationship("PlantSensorAssignment", back_populates="plant", cascade="all, delete-orphan")
    observations = relationship("PlantObservation", back_populates="plant", cascade="all, delete-orphan")
    observation_accesses = relationship("PlantObservationAccess", back_populates="plant", cascade="all, delete-orphan")


class PlantSensorAssignment(Base):
    __tablename__ = "plant_sensor_assignment"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plant_id = Column(
        UUID(as_uuid=True), ForeignKey("plant.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sensor_id = Column(
        UUID(as_uuid=True), ForeignKey("sensor.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assigned_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    unassigned_at = Column(DateTime(timezone=True), nullable=True)
    assigned_by_user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    plant = relationship("Plant", back_populates="sensor_assignments")
    sensor = relationship("Sensor")
