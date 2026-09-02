"""Model for WAV file metadata stored in MinIO/S3."""

import uuid

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

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
    content_sha256 = Column(String(64), nullable=True)
    sample_rate = Column(Integer, nullable=False, default=380)
    duration_seconds = Column(Float, nullable=False)
    coverage_ratio = Column(Float, nullable=False, default=1.0, server_default="1")
    timing_status = Column(
        String(20), nullable=False, default="complete", server_default="complete"
    )
    file_size_bytes = Column(Integer, nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False, index=True)
    ended_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    timestamp_source = Column(String(20), nullable=False, server_default="filename")
    feature_status = Column(String(20), nullable=False, default="pending", server_default="pending")
    feature_attempts = Column(Integer, nullable=False, default=0, server_default="0")
    feature_error = Column(String(500), nullable=True)
    feature_started_at = Column(DateTime(timezone=True), nullable=True)
    feature_verified_at = Column(DateTime(timezone=True), nullable=True)
    raw_deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)
    pcm_encoding_version = Column(
        String(50),
        nullable=False,
        default="unsigned-mv-linear-int16-v1",
        server_default="unsigned-mv-linear-int16-v1",
    )
    pcm_scale_mv = Column(Float, nullable=False, default=3300.0 / 32767.0)
    pcm_offset_mv = Column(Float, nullable=False, default=0.0, server_default="0")
    calibration_version = Column(
        String(50),
        nullable=False,
        default="nominal-adc-3v3-v1",
        server_default="nominal-adc-3v3-v1",
    )

    feature = relationship(
        "WavFeature",
        back_populates="wav_file",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )
    feature_versions = relationship(
        "WavFeatureVersion",
        back_populates="wav_file",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class WavFeature(Base):
    """Verified, long-lived features derived from one full-resolution WAV."""

    __tablename__ = "wav_feature"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    wav_file_id = Column(
        UUID(as_uuid=True),
        ForeignKey("wav_file.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    sensor_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    gateway_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    sensor_mac = Column(String(20), nullable=False, index=True)
    started_at = Column(DateTime(timezone=True), nullable=False, index=True)
    ended_at = Column(DateTime(timezone=True), nullable=False)

    extractor_version = Column(String(50), nullable=False)
    calibration_version = Column(String(50), nullable=False, default="nominal-adc-3v3-v1")
    parameter_hash = Column(String(64), nullable=False, default="")
    feature_checksum = Column(String(64), nullable=False)
    source_sha256 = Column(String(64), nullable=False)
    sample_rate = Column(Integer, nullable=False)
    sample_count = Column(BigInteger, nullable=False)
    duration_seconds = Column(Float, nullable=False)
    value_unit = Column(String(20), nullable=False, default="pcm_int16")

    mean = Column(Float, nullable=False)
    median = Column(Float, nullable=False)
    rms = Column(Float, nullable=False)
    standard_deviation = Column(Float, nullable=False)
    minimum = Column(Float, nullable=False)
    maximum = Column(Float, nullable=False)
    quantiles = Column(JSON, nullable=False)
    outlier_count = Column(BigInteger, nullable=False)
    outlier_ratio = Column(Float, nullable=False)
    clipping_count = Column(BigInteger, nullable=False)
    clipping_ratio = Column(Float, nullable=False)
    data_quality_status = Column(String(20), nullable=False, default="unknown")
    technical_fault_score = Column(Float, nullable=False, default=0.0, server_default="0")
    technical_fault_reasons = Column(JSON, nullable=False, default=list)
    biological_candidate_score = Column(Float, nullable=False, default=0.0, server_default="0")
    biological_candidate_reasons = Column(JSON, nullable=False, default=list)
    source_quality_counts = Column(JSON, nullable=False, default=dict)

    coverage_ratio = Column(Float, nullable=False)
    timing_status = Column(String(20), nullable=False)
    missing_duration_seconds = Column(Float, nullable=False)
    flatline_count = Column(Integer, nullable=False)
    flatline_seconds = Column(Float, nullable=False)
    sequence_observations = Column(Integer, nullable=False)
    sequence_gap_count = Column(Integer, nullable=False)
    sequence_missing_count = Column(BigInteger, nullable=False)
    sequence_reset_count = Column(Integer, nullable=False)
    source_dropped_samples_delta = Column(BigInteger, nullable=True)

    spectral_energy_total = Column(Float, nullable=False)
    dominant_frequency_hz = Column(Float, nullable=False)
    spectral_bands = Column(JSON, nullable=False)

    is_anomaly = Column(Boolean, nullable=False, default=False, server_default="false", index=True)
    anomaly_score = Column(Float, nullable=False, default=0.0, server_default="0")
    anomaly_reasons = Column(JSON, nullable=False)
    anomaly_s3_key = Column(String(500), nullable=True, unique=True)
    anomaly_sha256 = Column(String(64), nullable=True)
    anomaly_file_size_bytes = Column(Integer, nullable=True)
    anomaly_verified_at = Column(DateTime(timezone=True), nullable=True)
    anomaly_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)

    flac_s3_key = Column(String(500), nullable=True, unique=True)
    flac_sha256 = Column(String(64), nullable=True)
    flac_file_size_bytes = Column(Integer, nullable=True)
    flac_verified_at = Column(DateTime(timezone=True), nullable=True)
    flac_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)

    verified_at = Column(DateTime(timezone=True), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    wav_file = relationship("WavFile", back_populates="feature")


class WavFeatureVersion(Base):
    """Immutable verified feature snapshot for one extractor configuration."""

    __tablename__ = "wav_feature_version"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    wav_file_id = Column(
        UUID(as_uuid=True),
        ForeignKey("wav_file.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    extractor_version = Column(String(50), nullable=False)
    calibration_version = Column(String(50), nullable=False)
    parameter_hash = Column(String(64), nullable=False)
    source_sha256 = Column(String(64), nullable=False)
    feature_checksum = Column(String(64), nullable=False)
    feature_payload = Column(JSON, nullable=False)
    verified_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    wav_file = relationship("WavFile", back_populates="feature_versions")

    __table_args__ = (
        UniqueConstraint(
            "wav_file_id",
            "extractor_version",
            "calibration_version",
            "parameter_hash",
            name="uq_wav_feature_version_identity",
        ),
    )
