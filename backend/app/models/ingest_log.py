"""Model for tracking IoT data ingestion (idempotency & raw files)."""

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class IngestLog(Base):
    """Log of all IoT ingestion requests for duplicate detection and tracing."""

    __tablename__ = "ingest_log"

    measurement_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id = Column(
        UUID(as_uuid=True), ForeignKey("device.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status = Column(String(50), nullable=False, default="success")
    raw_file_reference = Column(String(500), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
