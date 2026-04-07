"""User & Organization models with RBAC roles."""

import enum
import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class Role(enum.StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class Organization(Base):
    __tablename__ = "organization"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    users = relationship("User", back_populates="organization", cascade="all, delete-orphan")
    zones = relationship("Zone", back_populates="organization", cascade="all, delete-orphan")


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(200), nullable=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(
        Enum(
            Role,
            name="user_role",
            create_type=False,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
        default=Role.MEMBER,
    )
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organization.id"), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    last_login = Column(DateTime(timezone=True), nullable=True)

    organization = relationship("Organization", back_populates="users", lazy="joined")


class EmailVerification(Base):
    __tablename__ = "email_verification"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token = Column(String(255), nullable=False, index=True, unique=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    user = relationship("User")
