"""Organization management endpoints."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.database import get_db
from app.models.user import User, Organization
from app.auth import get_current_user

router = APIRouter(prefix="/api/organizations", tags=["organizations"])


class OrgCreate(BaseModel):
    name: str


class OrgResponse(BaseModel):
    id: str
    name: str
    created_at: str

    class Config:
        from_attributes = True


@router.get("", response_model=Optional[OrgResponse])
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
    current_user: User = Depends(get_current_user),
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
