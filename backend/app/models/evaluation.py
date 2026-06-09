"""Structured plant evaluation model for ML-ready scoring."""

import uuid

from sqlalchemy import Column, DateTime, Float, ForeignKey, SmallInteger, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class PlantEvaluation(Base):
    __tablename__ = "plant_evaluation"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plant_id = Column(
        UUID(as_uuid=True), ForeignKey("plant.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sensor_id = Column(
        UUID(as_uuid=True), ForeignKey("sensor.id", ondelete="SET NULL"), nullable=True
    )
    zone_id = Column(UUID(as_uuid=True), ForeignKey("zone.id", ondelete="CASCADE"), nullable=False)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("plant_observation_session.id", ondelete="SET NULL"),
        nullable=True,
    )

    evaluated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Numerical scores (1–5), directly usable for ML
    overall_score = Column(SmallInteger, nullable=False)
    color_score = Column(SmallInteger, nullable=False)
    structure_score = Column(SmallInteger, nullable=False)
    growth_score = Column(SmallInteger, nullable=False)
    water_score = Column(SmallInteger, nullable=False)

    # Anomaly bitmask: spots=1, holes=2, mold=4, pests=8, none=16
    anomalies_vector = Column(SmallInteger, nullable=False, default=0)

    # Raw choice keys for traceability
    color_raw = Column(String(50), nullable=False)
    structure_raw = Column(String(50), nullable=False)
    growth_raw = Column(String(50), nullable=False)
    water_raw = Column(String(50), nullable=False)
    anomalies_raw = Column(String(200), nullable=False)

    # Computed consistency score (0.0–1.0)
    confidence_score = Column(Float, nullable=True)

    # Adaptive detail notes (shown when overall_score <= 2)
    detail_notes = Column(Text, nullable=True)

    # Request metadata
    used_ip = Column(String(45), nullable=True)
    user_agent = Column(String(500), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    plant = relationship("Plant")
