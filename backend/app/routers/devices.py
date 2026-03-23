"""Device management & pairing endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.device import (
    DeviceResponse,
    PairDeviceRequest,
    PairDeviceResponse,
    PairingCodeRequest,
    PairingCodeResponse,
)
from app.services.device_service import generate_pairing_code, list_devices, pair_device

router = APIRouter(prefix="/devices", tags=["devices"])


@router.get("", response_model=list[DeviceResponse])
async def handle_list_devices(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all devices in user's org greenhouses."""
    return list_devices(db, current_user)


@router.post("/pairing-code", response_model=PairingCodeResponse, status_code=201)
async def handle_generate_pairing_code(
    data: PairingCodeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Generate a short-lived pairing code for a greenhouse."""
    return generate_pairing_code(db, current_user, data)


@router.post("/pair", response_model=PairDeviceResponse, status_code=201)
async def handle_pair_device(
    data: PairDeviceRequest,
    db: Session = Depends(get_db),
):
    """Device submits pairing code to register itself. Returns an API key for future data ingest."""
    return pair_device(db, data)
