"""One server-side policy for zone metadata, measurements and original recordings."""

import uuid

from fastapi import HTTPException
from sqlalchemy import and_, false, select

from app.models.master import Zone
from app.models.user import Role
from app.models.zone_access import ZoneAccess


def zone_access_filter(user):
    if not user.organization_id:
        return false()
    tenant = Zone.organization_id == user.organization_id
    if user.role in (Role.OWNER, Role.ADMIN):
        return tenant
    return and_(
        tenant, Zone.id.in_(select(ZoneAccess.zone_id).where(ZoneAccess.user_id == user.id))
    )


def allowed_zone_ids(db, user):
    return [row[0] for row in db.execute(select(Zone.id).where(zone_access_filter(user)))]


def require_zone_access(db, user, zone_id):
    try:
        identity = uuid.UUID(str(zone_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(404, "Zone not found") from None
    zone = db.execute(
        select(Zone).where(Zone.id == identity, zone_access_filter(user))
    ).scalar_one_or_none()
    if zone is None:
        raise HTTPException(404, "Zone not found")
    return zone
