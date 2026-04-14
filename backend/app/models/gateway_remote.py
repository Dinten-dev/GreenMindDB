"""Gateway remote management models.

Covers: app releases, config releases, desired state,
remote commands, state reports, and update logs for
Raspberry Pi gateways managed over-the-air.
"""

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.database import Base


class GatewayAppRelease(Base):
    """Versioned software release for Raspberry Pi gateways."""

    __tablename__ = "gateway_app_release"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version = Column(String(50), nullable=False, unique=True, index=True)
    artifact_path = Column(String(500), nullable=False)
    sha256 = Column(String(64), nullable=False)
    signature = Column(Text, nullable=True)  # Ed25519 base64
    mandatory = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=False)
    channel = Column(String(20), nullable=False, default="stable")
    min_version = Column(String(50), nullable=True)
    file_size_bytes = Column(Integer, nullable=True)
    changelog = Column(Text, nullable=True)
    created_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class GatewayConfigRelease(Base):
    """Versioned configuration snapshot for gateways."""

    __tablename__ = "gateway_config_release"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version = Column(String(50), nullable=False, unique=True, index=True)
    config_payload = Column(JSONB, nullable=False)
    schema_version = Column(String(20), nullable=False, default="1")
    compatible_app_min = Column(String(50), nullable=True)
    compatible_app_max = Column(String(50), nullable=True)
    sha256 = Column(String(64), nullable=False)
    is_active = Column(Boolean, nullable=False, default=False)
    created_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class GatewayDesiredState(Base):
    """Per-gateway target state — agent polls and converges towards this."""

    __tablename__ = "gateway_desired_state"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    gateway_id = Column(
        UUID(as_uuid=True),
        ForeignKey("gateway.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    desired_app_version = Column(String(50), nullable=True)
    desired_config_version = Column(String(50), nullable=True)
    maintenance_mode = Column(Boolean, nullable=False, default=False)
    reboot_allowed = Column(Boolean, nullable=False, default=False)
    blocked = Column(Boolean, nullable=False, default=False)
    rollout_ring = Column(String(50), nullable=False, default="stable")
    force_downgrade = Column(Boolean, nullable=False, default=False)

    # Update window (nullable = anytime)
    update_window_start = Column(String(5), nullable=True)  # HH:MM
    update_window_end = Column(String(5), nullable=True)
    update_timezone = Column(String(50), nullable=False, default="UTC")
    allow_download_outside_window = Column(Boolean, nullable=False, default=True)
    allow_apply_outside_window = Column(Boolean, nullable=False, default=False)
    allow_reboot_outside_window = Column(Boolean, nullable=False, default=False)

    updated_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_at = Column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    gateway = relationship("Gateway", backref="desired_state_rel")


class GatewayCommand(Base):
    """Remote command queued for a gateway (allowlisted, TTL-enforced)."""

    __tablename__ = "gateway_command"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    gateway_id = Column(
        UUID(as_uuid=True),
        ForeignKey("gateway.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    command_type = Column(String(50), nullable=False)
    payload = Column(JSONB, nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    created_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    expires_at = Column(DateTime(timezone=True), nullable=False)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    executed_at = Column(DateTime(timezone=True), nullable=True)
    result_message = Column(Text, nullable=True)

    gateway = relationship("Gateway")


class GatewayStateReport(Base):
    """Periodic state report from a gateway agent."""

    __tablename__ = "gateway_state_report"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    gateway_id = Column(
        UUID(as_uuid=True),
        ForeignKey("gateway.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    app_version = Column(String(50), nullable=True)
    config_version = Column(String(50), nullable=True)
    agent_version = Column(String(50), nullable=True)
    status = Column(String(50), nullable=False)
    health_status = Column(String(20), nullable=True)
    disk_free_mb = Column(Integer, nullable=True)
    cpu_temp_c = Column(Float, nullable=True)
    ram_usage_pct = Column(Float, nullable=True)
    uptime_seconds = Column(Integer, nullable=True)
    last_error = Column(Text, nullable=True)
    update_download_status = Column(String(20), nullable=True)
    update_apply_status = Column(String(20), nullable=True)
    signature_status = Column(String(20), nullable=True)
    reported_at = Column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False, index=True
    )

    gateway = relationship("Gateway")


class GatewayUpdateLog(Base):
    """Immutable log of every update attempt (app or config)."""

    __tablename__ = "gateway_update_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    gateway_id = Column(
        UUID(as_uuid=True),
        ForeignKey("gateway.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    update_type = Column(String(20), nullable=False)  # app / config
    from_version = Column(String(50), nullable=True)
    to_version = Column(String(50), nullable=False)
    status = Column(String(50), nullable=False)
    error_message = Column(Text, nullable=True)
    started_at = Column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)

    gateway = relationship("Gateway")
