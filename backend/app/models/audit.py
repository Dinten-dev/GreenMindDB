"""Audit and ingest logging models."""
import uuid

from sqlalchemy import (
    Column, DateTime, ForeignKey, String, Text, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.database import Base


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    time = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False, index=True)
    actor_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_type = Column(String(20), nullable=False, default="USER")  # USER / GATEWAY / SYSTEM
    action = Column(String(100), nullable=False)
    resource_type = Column(String(50), nullable=True)
    resource_id = Column(String(100), nullable=True)
    greenhouse_id = Column(UUID(as_uuid=True), ForeignKey("greenhouse.id", ondelete="SET NULL"), nullable=True)
    ip = Column(String(50), nullable=True)
    user_agent = Column(String(500), nullable=True)
    details = Column(JSONB, server_default=text("'{}'::jsonb"))


class IngestLog(Base):
    __tablename__ = "ingest_log"

    request_id = Column(UUID(as_uuid=True), primary_key=True)
    received_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    endpoint = Column(String(100), nullable=False)
    source = Column(String(100), nullable=True)
    greenhouse_id = Column(UUID(as_uuid=True), ForeignKey("greenhouse.id", ondelete="SET NULL"), nullable=True)
    status = Column(String(20), nullable=False, default="received")
    details = Column(JSONB, server_default=text("'{}'::jsonb"))
