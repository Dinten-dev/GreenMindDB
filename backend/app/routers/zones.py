"""Zone management endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.gateway import PairingCodeResponse
from app.schemas.zone import ZoneCreate, ZoneOverview, ZoneResponse
from app.services.gateway_service import generate_pairing_code
from app.services.zone_service import (
    create_zone,
    delete_zone,
    get_zone,
    get_zone_overview,
    list_zones,
)

router = APIRouter(prefix="/zones", tags=["zones"])


@router.get("", response_model=list[ZoneResponse])
async def handle_list_zones(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all zones in user's organization."""
    return list_zones(db, current_user)


@router.post("", response_model=ZoneResponse, status_code=201)
async def handle_create_zone(
    data: ZoneCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new zone in the user's organization."""
    return create_zone(db, current_user, data)


@router.get("/{zone_id}", response_model=ZoneResponse)
async def handle_get_zone(
    zone_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get a single zone."""
    return get_zone(db, current_user, zone_id)


@router.delete("/{zone_id}", status_code=204)
async def handle_delete_zone(
    zone_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a zone and cascade delete all its resources."""
    return delete_zone(db, current_user, zone_id)


@router.get("/{zone_id}/overview", response_model=ZoneOverview)
async def handle_get_zone_overview(
    zone_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get overview stats for a zone."""
    return get_zone_overview(db, current_user, zone_id)


@router.post("/{zone_id}/pairing-codes", response_model=PairingCodeResponse, status_code=201)
async def handle_generate_pairing_code(
    zone_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Generate a short-lived pairing code for a zone."""
    return generate_pairing_code(db, current_user, zone_id)
