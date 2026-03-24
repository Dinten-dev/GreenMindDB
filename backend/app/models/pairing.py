"""Device pairing code model."""

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class PairingCode(Base):
    __tablename__ = "pairing_code"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(8), nullable=False, unique=True, index=True)
    greenhouse_id = Column(
        UUID(as_uuid=True), ForeignKey("greenhouse.id", ondelete="CASCADE"), nullable=False
    )
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    device_id = Column(
        UUID(as_uuid=True), ForeignKey("device.id", ondelete="SET NULL"), nullable=True
    )
    created_by_user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
