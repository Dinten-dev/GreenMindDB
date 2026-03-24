"""Device management & pairing endpoints."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user, verify_password
from app.database import get_db
from app.models.master import Device
from app.models.user import User
from app.schemas.device import (
    DeviceResponse,
    PairDeviceRequest,
    PairDeviceResponse,
)
from app.services.device_service import list_devices, pair_device

router = APIRouter(prefix="/devices", tags=["devices"])


@router.get("", response_model=list[DeviceResponse])
async def handle_list_devices(
    greenhouse_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List devices. Optionally filter by greenhouse_id."""
    return list_devices(db, current_user, greenhouse_id=greenhouse_id)


class HeartbeatRequest(BaseModel):
    device_serial: str


@router.post("/heartbeat", status_code=200)
async def handle_heartbeat(
    data: HeartbeatRequest,
    db: Session = Depends(get_db),
    x_api_key: str | None = Header(None),
):
    """Device sends a heartbeat to update last_seen_at."""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-Api-Key header")

    device = db.query(Device).filter(Device.serial == data.device_serial).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    if not device.is_active or device.status == "revoked":
        raise HTTPException(status_code=403, detail="Device deactivated")
    if not device.api_key_hash or not verify_password(x_api_key, device.api_key_hash):
        raise HTTPException(status_code=401, detail="Invalid API key")

    device.last_seen = datetime.now(UTC)
    device.status = "online"
    db.commit()

    return {"status": "ok"}


@router.post("/pair", response_model=PairDeviceResponse, status_code=201)
async def handle_pair_device(
    data: PairDeviceRequest,
    db: Session = Depends(get_db),
):
    """Device submits pairing code to register itself. Returns an API key for future data ingest."""
    return pair_device(db, data)
