"""Audit log model for tracking changes."""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, JSON
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.database import Base


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    entity_type = Column(String, nullable=False)  # 'species' or 'target_range'
    entity_id = Column(Integer, nullable=False)
    species_id = Column(Integer, nullable=False, index=True)  # For filtering by plant
    action = Column(String, nullable=False)  # 'CREATE', 'UPDATE', 'DELETE'
    diff_json = Column(JSON, nullable=False)  # {before: {...}, after: {...}}
    
    # Relationships
    user = relationship("User")
