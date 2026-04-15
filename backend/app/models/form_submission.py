"""Model for persisting contact and early-access form submissions."""

import uuid

from sqlalchemy import Column, DateTime, String, Text, text
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class FormSubmission(Base):
    """Stores every inbound form submission as a durable backup to email."""

    __tablename__ = "form_submission"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    form_type = Column(String(30), nullable=False, index=True)  # "contact" | "early_access"
    name = Column(String(200), nullable=False)
    email = Column(String(320), nullable=False)
    company = Column(String(200), nullable=True)
    country = Column(String(100), nullable=True)
    message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
