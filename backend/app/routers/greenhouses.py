"""Greenhouse management endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.gateway import PairingCodeResponse
from app.schemas.greenhouse import GreenhouseCreate, GreenhouseOverview, GreenhouseResponse
from app.services.gateway_service import generate_pairing_code
from app.services.greenhouse_service import (
    create_greenhouse,
    delete_greenhouse,
    get_greenhouse,
    get_greenhouse_overview,
    list_greenhouses,
)

router = APIRouter(prefix="/greenhouses", tags=["greenhouses"])


@router.get("", response_model=list[GreenhouseResponse])
async def handle_list_greenhouses(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all greenhouses in user's organization."""
    return list_greenhouses(db, current_user)


@router.post("", response_model=GreenhouseResponse, status_code=201)
async def handle_create_greenhouse(
    data: GreenhouseCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new greenhouse in the user's organization."""
    return create_greenhouse(db, current_user, data)


@router.get("/{greenhouse_id}", response_model=GreenhouseResponse)
async def handle_get_greenhouse(
    greenhouse_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get a single greenhouse."""
    return get_greenhouse(db, current_user, greenhouse_id)


@router.delete("/{greenhouse_id}", status_code=204)
async def handle_delete_greenhouse(
    greenhouse_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a greenhouse and cascade delete all its resources."""
    return delete_greenhouse(db, current_user, greenhouse_id)


@router.get("/{greenhouse_id}/overview", response_model=GreenhouseOverview)
async def handle_get_greenhouse_overview(
    greenhouse_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get overview stats for a greenhouse."""
    return get_greenhouse_overview(db, current_user, greenhouse_id)


@router.post("/{greenhouse_id}/pairing-codes", response_model=PairingCodeResponse, status_code=201)
async def handle_generate_pairing_code(
    greenhouse_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Generate a short-lived pairing code for a greenhouse."""
    return generate_pairing_code(db, current_user, greenhouse_id)
