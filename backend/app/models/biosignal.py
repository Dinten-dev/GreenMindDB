"""Bio-signal specific database models for AD8232 sessions and aggregates."""

import uuid

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class BioSession(Base):
    """Represents a continuous recording session from an AD8232 sensor."""

    __tablename__ = "bio_session"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sensor_mac = Column(String(17), nullable=False, index=True)
    gateway_id = Column(
        UUID(as_uuid=True), ForeignKey("gateway.id", ondelete="CASCADE"), nullable=False
    )

    hardware_model = Column(String(50), nullable=False, default="AD8232")
    sample_rate_hz = Column(Integer, nullable=False, default=380)

    start_time = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=True)

    total_samples = Column(Integer, nullable=False, default=0)
    invalid_samples = Column(Integer, nullable=False, default=0)

    # We store the local file path or S3 key to the raw data chunks
    # or the final exported WAV file.
    raw_storage_key = Column(String(500), nullable=True)
    wav_storage_key = Column(String(500), nullable=True)

    gateway = relationship("Gateway")


class BioAggregate(Base):
    """1-second aggregates of the high-frequency bio-signal for quick database querying."""

    __tablename__ = "bio_aggregate"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("bio_session.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)

    mean_mv = Column(Float, nullable=False)
    min_mv = Column(Float, nullable=False)
    max_mv = Column(Float, nullable=False)

    # Artifact counts in this 1-second window
    samples_total = Column(Integer, nullable=False)
    samples_invalid = Column(Integer, nullable=False)

    session = relationship("BioSession")
