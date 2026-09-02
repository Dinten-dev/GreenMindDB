"""Operational audit and worker heartbeat models."""

import uuid

from sqlalchemy import JSON, BigInteger, Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class RetentionRun(Base):
    __tablename__ = "retention_run"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(20), nullable=False, default="running")
    dry_run = Column(Integer, nullable=False, default=1)
    result = Column(JSON, nullable=False, default=dict)
    error = Column(Text, nullable=True)


class RetentionRunItem(Base):
    __tablename__ = "retention_run_item"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("retention_run.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    category = Column(String(50), nullable=False)
    affected_count = Column(Integer, nullable=False, default=0)
    affected_bytes = Column(BigInteger, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BackgroundWorkerHeartbeat(Base):
    __tablename__ = "background_worker_heartbeat"

    worker_name = Column(String(50), primary_key=True)
    status = Column(String(20), nullable=False)
    details = Column(JSON, nullable=False, default=dict)
    heartbeat_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
