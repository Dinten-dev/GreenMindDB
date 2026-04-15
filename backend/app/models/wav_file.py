"""Model for WAV file metadata stored in MinIO/S3."""

import uuid

from sqlalchemy import Column, DateTime, Float, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class WavFile(Base):
    """Metadata for a WAV file stored in object storage (MinIO/S3)."""

    __tablename__ = "wav_file"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sensor_id = Column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    gateway_id = Column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    sensor_mac = Column(String(20), nullable=False, index=True)
    s3_key = Column(String(500), nullable=False, unique=True)
    sample_rate = Column(Integer, nullable=False, default=380)
    duration_seconds = Column(Float, nullable=False)
    file_size_bytes = Column(Integer, nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False, index=True)
    ended_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
