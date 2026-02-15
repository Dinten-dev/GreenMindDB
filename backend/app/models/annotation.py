"""Annotation & labeling models for model-validation workflows."""
import enum
import uuid

from sqlalchemy import (
    Boolean, Column, DateTime, Enum, ForeignKey, Integer,
    SmallInteger, String, Text, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.database import Base


class AnnotationStatus(str, enum.Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    REVIEWED = "reviewed"
    REJECTED = "rejected"


class ReviewDecision(str, enum.Enum):
    APPROVE = "approve"
    REJECT = "reject"


class LabelSchema(Base):
    __tablename__ = "label_schema"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    greenhouse_id = Column(UUID(as_uuid=True), ForeignKey("greenhouse.id", ondelete="SET NULL"), nullable=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    values = Column(JSONB, nullable=False)  # e.g. ["water_stress","nitrogen_deficiency",...]
    version = Column(Integer, nullable=False, default=1)
    is_active = Column(Boolean, default=True, nullable=False)


class Annotation(Base):
    __tablename__ = "annotation"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    greenhouse_id = Column(UUID(as_uuid=True), ForeignKey("greenhouse.id", ondelete="CASCADE"), nullable=False, index=True)
    plant_id = Column(UUID(as_uuid=True), ForeignKey("plant.id", ondelete="CASCADE"), nullable=False, index=True)
    sensor_id = Column(UUID(as_uuid=True), ForeignKey("sensor.id", ondelete="SET NULL"), nullable=True)
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=False)
    label_key = Column(String(100), nullable=False)
    label_value = Column(String(100), nullable=False)
    confidence = Column(SmallInteger, nullable=True)
    notes = Column(Text, nullable=True)
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    status = Column(
        Enum(
            AnnotationStatus,
            name="annotation_status",
            create_type=False,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
        default=AnnotationStatus.DRAFT,
    )

    reviews = relationship("AnnotationReview", back_populates="annotation", cascade="all, delete-orphan")


class AnnotationReview(Base):
    __tablename__ = "annotation_review"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    annotation_id = Column(UUID(as_uuid=True), ForeignKey("annotation.id", ondelete="CASCADE"), nullable=False, index=True)
    reviewed_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decision = Column(
        Enum(
            ReviewDecision,
            name="review_decision",
            create_type=False,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
    )
    notes = Column(Text, nullable=True)
    reviewed_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    annotation = relationship("Annotation", back_populates="reviews")
