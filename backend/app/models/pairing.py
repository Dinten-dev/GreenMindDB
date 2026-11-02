"""Gateway pairing code model."""

import uuid
from enum import Enum

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class PairingPurpose(str, Enum):
    GATEWAY = "gateway"
    SENSOR = "sensor"
    LEGACY = "legacy"


class PairingCode(Base):
    __tablename__ = "pairing_code"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('gateway', 'sensor', 'legacy') AND "
            "(purpose != 'legacy' OR used_at IS NOT NULL)",
            name="ck_pairing_code_purpose_v1",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(8), nullable=False, unique=True, index=True)
    # No default: old issuers must fail closed after the schema upgrade.
    purpose = Column(String(16), nullable=False)
    zone_id = Column(UUID(as_uuid=True), ForeignKey("zone.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    gateway_id = Column(
        UUID(as_uuid=True), ForeignKey("gateway.id", ondelete="SET NULL"), nullable=True
    )
    created_by_user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
