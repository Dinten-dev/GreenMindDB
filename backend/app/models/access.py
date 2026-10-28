"""Invitation history and revocable browser/API sessions."""

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class AccessInvitation(Base):
    __tablename__ = "access_invitation"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    submission_id = Column(
        UUID(as_uuid=True), ForeignKey("form_submission.id"), nullable=False, index=True
    )
    email = Column(String(255), nullable=False, index=True)
    token_hash = Column(String(64), unique=True, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    delivery_status = Column(String(20), nullable=False, default="pending")
    delivery_error = Column(String(80))
    sent_at = Column(DateTime(timezone=True))
    redeemed_at = Column(DateTime(timezone=True))
    redeemed_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    revoked_at = Column(DateTime(timezone=True))


class AuthSession(Base):
    __tablename__ = "auth_session"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
