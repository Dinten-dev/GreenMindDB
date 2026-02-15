"""Domain models: EventLog, GroundTruthDaily, LabResults."""
import uuid

from sqlalchemy import (
    Column, Date, DateTime, Float, ForeignKey, Integer,
    SmallInteger, String, Text, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.database import Base


class EventLog(Base):
    __tablename__ = "event_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    greenhouse_id = Column(UUID(as_uuid=True), ForeignKey("greenhouse.id", ondelete="CASCADE"), nullable=False, index=True)
    time = Column(DateTime(timezone=True), nullable=False, index=True)
    type = Column(String(50), nullable=False, index=True)
    payload = Column(JSONB, server_default=text("'{}'::jsonb"))
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    source_device_id = Column(UUID(as_uuid=True), ForeignKey("device.id", ondelete="SET NULL"), nullable=True)
    request_id = Column(UUID(as_uuid=True), unique=True, nullable=False)


class GroundTruthDaily(Base):
    __tablename__ = "ground_truth_daily"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    greenhouse_id = Column(UUID(as_uuid=True), ForeignKey("greenhouse.id", ondelete="CASCADE"), nullable=False, index=True)
    plant_id = Column(UUID(as_uuid=True), ForeignKey("plant.id", ondelete="CASCADE"), nullable=False, index=True)
    date = Column(Date, nullable=False)
    vitality_score = Column(SmallInteger, nullable=False)   # 1-5
    growth_score = Column(SmallInteger, nullable=False)     # 1-5
    pest_score = Column(SmallInteger, nullable=False)       # 1-5
    disease_score = Column(SmallInteger, nullable=False)    # 1-5
    notes = Column(Text, nullable=True)
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    __table_args__ = (
        UniqueConstraint("greenhouse_id", "plant_id", "date", name="uq_ground_truth_plant_date"),
    )


class LabResults(Base):
    __tablename__ = "lab_results"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    greenhouse_id = Column(UUID(as_uuid=True), ForeignKey("greenhouse.id", ondelete="CASCADE"), nullable=False, index=True)
    plant_id = Column(UUID(as_uuid=True), ForeignKey("plant.id", ondelete="CASCADE"), nullable=False, index=True)
    time = Column(DateTime(timezone=True), nullable=False)
    analyte = Column(String(100), nullable=False)
    value = Column(Float, nullable=False)
    unit = Column(String(20), nullable=False)
    lab_meta = Column(JSONB, server_default=text("'{}'::jsonb"))
