"""Organization management endpoints."""

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_role
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.master import Zone
from app.models.user import Organization, Role, User
from app.models.zone_access import ZoneAccess
from app.schemas.organization import MemberZoneAccess, OrgCreate, OrgResponse, UpdateMemberZones

router = APIRouter(prefix="/organizations", tags=["organizations"])
_tenant_manager = require_role([Role.OWNER, Role.ADMIN])


@router.get("/members", response_model=list[MemberZoneAccess])
def list_member_access(
    current_user: User = Depends(_tenant_manager), db: Session = Depends(get_db)
):
    if not current_user.organization_id:
        return []
    members = (
        db.query(User)
        .filter(User.organization_id == current_user.organization_id)
        .order_by(User.email)
        .all()
    )
    grants = (
        db.query(ZoneAccess)
        .join(Zone)
        .filter(Zone.organization_id == current_user.organization_id)
        .all()
    )
    by_user = {}
    for grant in grants:
        by_user.setdefault(grant.user_id, []).append(str(grant.zone_id))
    return [
        MemberZoneAccess(
            id=str(user.id),
            name=user.name,
            email=user.email,
            role=user.role,
            all_zones=user.role in (Role.OWNER, Role.ADMIN),
            zone_ids=sorted(by_user.get(user.id, [])),
        )
        for user in members
    ]


@router.put("/members/{user_id}/zones", response_model=MemberZoneAccess)
def update_member_access(
    user_id: uuid.UUID,
    data: UpdateMemberZones,
    current_user: User = Depends(_tenant_manager),
    db: Session = Depends(get_db),
):
    if not current_user.organization_id:
        raise HTTPException(404, "Member not found")
    member = (
        db.query(User)
        .filter(User.id == user_id, User.organization_id == current_user.organization_id)
        .with_for_update()
        .first()
    )
    if member is None:
        raise HTTPException(404, "Member not found")
    if member.role in (Role.OWNER, Role.ADMIN):
        raise HTTPException(409, "Eigentümer und Administratoren haben Zugriff auf alle Zonen.")
    selected = set(data.zone_ids)
    valid = {
        row[0]
        for row in db.query(Zone.id)
        .filter(Zone.organization_id == current_user.organization_id, Zone.id.in_(selected))
        .all()
    }
    if valid != selected:
        raise HTTPException(404, "Zone not found")
    previous = sorted(
        str(row[0])
        for row in db.query(ZoneAccess.zone_id).filter(ZoneAccess.user_id == user_id).all()
    )
    db.query(ZoneAccess).filter(ZoneAccess.user_id == user_id).delete(synchronize_session=False)
    db.add_all(ZoneAccess(user_id=user_id, zone_id=zone_id) for zone_id in selected)
    db.add(
        AuditLog(
            user_id=current_user.id,
            action="zone_access.update",
            entity_type="user",
            entity_id=str(user_id),
            details=json.dumps({"before": previous, "after": sorted(map(str, selected))}),
        )
    )
    db.commit()
    return MemberZoneAccess(
        id=str(member.id),
        name=member.name,
        email=member.email,
        role=member.role,
        all_zones=False,
        zone_ids=sorted(map(str, selected)),
    )


@router.get("", response_model=OrgResponse | None)
async def get_organization(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get user's organization."""
    if not current_user.organization_id:
        return None
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        return None
    return OrgResponse(id=str(org.id), name=org.name, created_at=org.created_at.isoformat())


@router.post("", response_model=OrgResponse, status_code=201)
async def create_organization(
    data: OrgCreate,
    current_user: User = Depends(_tenant_manager),
    db: Session = Depends(get_db),
):
    """Create a new organization and assign the current user as owner."""
    if current_user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User already belongs to an organization",
        )

    org = Organization(name=data.name)
    db.add(org)
    db.flush()

    current_user.organization_id = org.id
    db.commit()
    db.refresh(org)

    return OrgResponse(id=str(org.id), name=org.name, created_at=org.created_at.isoformat())
