"""Media metadata & export job tracking models."""
import uuid

from sqlalchemy import (
    BigInteger, Column, DateTime, ForeignKey, Integer, String, Text, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.database import Base


class ObjectMeta(Base):
    __tablename__ = "object_meta"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    time = Column(DateTime(timezone=True), nullable=False, index=True)
    greenhouse_id = Column(UUID(as_uuid=True), ForeignKey("greenhouse.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(String(50), nullable=False)  # image, video, export, etc.
    storage_key = Column(String(500), nullable=False)
    content_type = Column(String(100), nullable=False)
    sha256 = Column(String(64), nullable=True)
    size_bytes = Column(BigInteger, nullable=True)
    plant_id = Column(UUID(as_uuid=True), ForeignKey("plant.id", ondelete="SET NULL"), nullable=True)
    zone_id = Column(UUID(as_uuid=True), ForeignKey("zone.id", ondelete="SET NULL"), nullable=True)
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class ExportJob(Base):
    __tablename__ = "export_job"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    greenhouse_id = Column(UUID(as_uuid=True), ForeignKey("greenhouse.id", ondelete="CASCADE"), nullable=False, index=True)
    requested_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    status = Column(String(20), nullable=False, default="running")
    params = Column(JSONB, nullable=False)
    storage_key = Column(String(500), nullable=True)
    schema_key = Column(String(500), nullable=True)
    row_count = Column(Integer, nullable=True)
    error = Column(JSONB, nullable=True)
