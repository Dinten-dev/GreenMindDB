"""Plant observation and login-free access models."""

import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class PlantObservationAccess(Base):
    __tablename__ = "plant_observation_access"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plant_id = Column(
        UUID(as_uuid=True), ForeignKey("plant.id", ondelete="CASCADE"), nullable=False, index=True
    )
    public_id = Column(UUID(as_uuid=True), nullable=False, unique=True, index=True, default=uuid.uuid4)
    is_active = Column(Boolean, nullable=False, default=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    usage_count = Column(Integer, nullable=False, default=0)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    created_by_user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    plant = relationship("Plant", back_populates="observation_accesses")


class PlantObservationSession(Base):
    __tablename__ = "plant_observation_session"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plant_id = Column(
        UUID(as_uuid=True), ForeignKey("plant.id", ondelete="CASCADE"), nullable=False, index=True
    )
    access_id = Column(
        UUID(as_uuid=True),
        ForeignKey("plant_observation_access.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    session_token = Column(String(255), nullable=False, unique=True, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    used_ip = Column(String(45), nullable=True)
    user_agent = Column(String(500), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)


class PlantObservation(Base):
    __tablename__ = "plant_observation"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plant_id = Column(
        UUID(as_uuid=True), ForeignKey("plant.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sensor_id = Column(
        UUID(as_uuid=True), ForeignKey("sensor.id", ondelete="SET NULL"), nullable=True
    )
    zone_id = Column(UUID(as_uuid=True), ForeignKey("zone.id", ondelete="CASCADE"), nullable=False)
    observation_session_id = Column(
        UUID(as_uuid=True), ForeignKey("plant_observation_session.id", ondelete="SET NULL"), nullable=True
    )
    observed_by_user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    observed_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    wellbeing_score = Column(Integer, nullable=False)
    stress_score = Column(Integer, nullable=True)
    plant_condition = Column(String(50), nullable=False)

    leaf_droop = Column(Boolean, nullable=True)
    leaf_color = Column(String(100), nullable=True)
    spots_present = Column(Boolean, nullable=True)
    soil_condition = Column(String(100), nullable=True)
    suspected_stress_type = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    plant = relationship("Plant", back_populates="observations")
    photos = relationship("PlantObservationPhoto", back_populates="observation", cascade="all, delete-orphan")


class PlantObservationPhoto(Base):
    __tablename__ = "plant_observation_photo"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    observation_id = Column(
        UUID(as_uuid=True), ForeignKey("plant_observation.id", ondelete="CASCADE"), nullable=False, index=True
    )
    storage_key = Column(String(500), nullable=False)
    mime_type = Column(String(100), nullable=False)
    file_size = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    observation = relationship("PlantObservation", back_populates="photos")
