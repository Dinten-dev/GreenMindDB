"""Explicit zone grants for organization members."""

from sqlalchemy import Column, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class ZoneAccess(Base):
    __tablename__ = "zone_access"

    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    zone_id = Column(
        UUID(as_uuid=True), ForeignKey("zone.id", ondelete="CASCADE"), primary_key=True
    )
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
