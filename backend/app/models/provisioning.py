import enum
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Enum, String
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class ProvisioningStatus(enum.StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"

class ProvisioningJob(Base):
    __tablename__ = "provisioning_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    mac_address = Column(String, index=True, nullable=True)
    ssid = Column(String, nullable=False)
    password = Column(String, nullable=False)
    pairing_code = Column(String(6), nullable=False)
    status = Column(Enum(ProvisioningStatus), default=ProvisioningStatus.PENDING, nullable=False)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
