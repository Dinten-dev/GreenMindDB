"""Direct-only tables, deliberately absent from the legacy Alembic metadata."""

import time
import uuid

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Device(Base):
    __tablename__ = "direct_device"
    id = Column(String(36), primary_key=True)
    organization_id = Column(String(36), nullable=False, index=True)
    zone_id = Column(String(36), nullable=False)
    legacy_sensor_id = Column(String(36))
    mode = Column(String(10), nullable=False)
    key_hash = Column(String(64), nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    spool_bytes = Column(BigInteger, nullable=False, default=0)


class Budget(Base):
    __tablename__ = "direct_budget"
    id = Column(Integer, primary_key=True)
    used_bytes = Column(BigInteger, nullable=False, default=0)


class Session(Base):
    __tablename__ = "direct_session"
    device_id = Column(String(36), ForeignKey("direct_device.id"), primary_key=True)
    id = Column(String(36), primary_key=True)
    config = Column(JSON, nullable=False)
    mode = Column(String(10), nullable=False)


class Chunk(Base):
    __tablename__ = "direct_chunk"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    device_id = Column(String(36), nullable=False, index=True)
    session_id = Column(String(36), nullable=False, index=True)
    sequence = Column(BigInteger, nullable=False)
    first_frame = Column(BigInteger, nullable=False)
    frame_count = Column(Integer, nullable=False)
    digest = Column(String(64), nullable=False)
    payload_sha256 = Column(String(64), nullable=False)
    payload = Column(LargeBinary)
    payload_bytes = Column(Integer, nullable=False)
    received_at = Column(Float, default=time.time, nullable=False)
    __table_args__ = (UniqueConstraint("device_id", "session_id", "sequence"),)


class Segment(Base):
    __tablename__ = "direct_segment"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    device_id = Column(String(36), ForeignKey("direct_device.id"), nullable=False, index=True)
    session_id = Column(String(36), nullable=False)
    bucket = Column(BigInteger, nullable=False)
    first_frame = Column(BigInteger, nullable=False)
    end_frame = Column(BigInteger, nullable=False)
    received_frames = Column(Integer, default=0, nullable=False)
    revision = Column(Integer, default=0, nullable=False)
    published_revision = Column(Integer, default=0, nullable=False)
    sealed = Column(Boolean, default=False, nullable=False)
    updated_at = Column(Float, default=time.time, nullable=False)
    retry_at = Column(Float, default=0, nullable=False)
    error = Column(String(80))
    __table_args__ = (UniqueConstraint("device_id", "session_id", "bucket"),)


class Piece(Base):
    __tablename__ = "direct_piece"
    segment_id = Column(String(36), ForeignKey("direct_segment.id"), primary_key=True)
    chunk_id = Column(String(36), ForeignKey("direct_chunk.id"), primary_key=True)
    payload_offset = Column(Integer, nullable=False)
    frame_count = Column(Integer, nullable=False)


class Revision(Base):
    __tablename__ = "direct_revision"
    segment_id = Column(String(36), ForeignKey("direct_segment.id"), primary_key=True)
    revision = Column(Integer, primary_key=True)
    manifest = Column(JSON, nullable=False)
    manifest_sha256 = Column(String(64), nullable=False)
    verified_at = Column(Float, default=time.time, nullable=False)
    raw_deleted_at = Column(Float)


class Heartbeat(Base):
    __tablename__ = "direct_heartbeat"
    name = Column(String(30), primary_key=True)
    updated_at = Column(Float, nullable=False)
    status = Column(String(30), nullable=False)
