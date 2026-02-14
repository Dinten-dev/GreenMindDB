"""Audit logging utilities."""
from typing import Any, Optional
from sqlalchemy.orm import Session

from app.models import AuditLog, User


def log_change(
    db: Session,
    user: Optional[User],
    entity_type: str,
    entity_id: int,
    species_id: int,
    action: str,
    before: Optional[dict] = None,
    after: Optional[dict] = None
) -> AuditLog:
    """Log a change to the audit log.
    
    Args:
        db: Database session
        user: Current user (None for system actions)
        entity_type: 'species' or 'target_range'
        entity_id: ID of the entity
        species_id: ID of the related species (for filtering)
        action: 'CREATE', 'UPDATE', or 'DELETE'
        before: State before change (None for CREATE)
        after: State after change (None for DELETE)
    """
    log = AuditLog(
        user_id=user.id if user else None,
        entity_type=entity_type,
        entity_id=entity_id,
        species_id=species_id,
        action=action,
        diff_json={"before": before, "after": after}
    )
    db.add(log)
    db.flush()
    return log


def entity_to_dict(entity: Any) -> dict:
    """Convert an SQLAlchemy entity to a dict for logging."""
    result = {}
    for column in entity.__table__.columns:
        value = getattr(entity, column.name)
        # Convert non-serializable types
        if hasattr(value, 'isoformat'):
            value = value.isoformat()
        result[column.name] = value
    return result
