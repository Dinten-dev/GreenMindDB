"""Firmware OTA models: Releases, Policies, and Deployment Logs."""

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class FirmwareRelease(Base):
    __tablename__ = "firmware_release"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version = Column(String(50), nullable=False, index=True)  # e.g., "1.2.0"
    board_type = Column(String(50), nullable=False)  # e.g., "ESP32_WROOM"
    hardware_revision = Column(String(50), nullable=False)  # e.g., "v1"

    file_path = Column(String(500), nullable=False)  # path relative to /firmware/ static dir
    sha256 = Column(String(64), nullable=False)  # strict hash for ESP32 validation

    is_active = Column(Boolean, nullable=False, default=True)
    mandatory = Column(Boolean, nullable=False, default=False)
    min_version = Column(String(50), nullable=True)  # required minimum version before applying

    changelog = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    reports = relationship("FirmwareReport", back_populates="release", cascade="all, delete-orphan")


class RolloutPolicy(Base):
    __tablename__ = "rollout_policy"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    release_id = Column(
        UUID(as_uuid=True),
        ForeignKey("firmware_release.id", ondelete="CASCADE"),
        nullable=False,
    )
    zone_id = Column(
        UUID(as_uuid=True),
        ForeignKey("zone.id", ondelete="CASCADE"),
        nullable=True,  # If null, applies to all zones
    )
    canary_percentage = Column(
        String(10), nullable=True, default="100"
    )  # simplified representation
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    release = relationship("FirmwareRelease")


class FirmwareReport(Base):
    __tablename__ = "firmware_report"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sensor_id = Column(
        UUID(as_uuid=True),
        ForeignKey("sensor.id", ondelete="SET NULL"),
        nullable=True,
    )
    gateway_id = Column(
        UUID(as_uuid=True),
        ForeignKey("gateway.id", ondelete="CASCADE"),
        nullable=False,
    )
    release_id = Column(
        UUID(as_uuid=True),
        ForeignKey("firmware_release.id", ondelete="CASCADE"),
        nullable=False,
    )

    status = Column(
        String(50), nullable=False
    )  # success, failed, hash_mismatch, rollback, incompatible
    error_message = Column(Text, nullable=True)
    reported_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    release = relationship("FirmwareRelease", back_populates="reports")
    # You can add relationship for sensor and gateway if needed
