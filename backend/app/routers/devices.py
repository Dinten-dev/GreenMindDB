"""Device management & pairing endpoints."""

import secrets
import string
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import get_current_user, get_password_hash
from app.database import get_db
from app.models.master import Device, Greenhouse, Sensor
from app.models.pairing import PairingCode
from app.models.user import User
from app.schemas.device import (
    DeviceResponse,
    PairDeviceRequest,
    PairDeviceResponse,
    PairingCodeRequest,
    PairingCodeResponse,
)

router = APIRouter(prefix="/api/devices", tags=["devices"])

PAIRING_CODE_LENGTH = 6
PAIRING_CODE_EXPIRY_MINUTES = 10


# ── Endpoints ────────────────────────────────────


@router.get("", response_model=list[DeviceResponse])
async def list_devices(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all devices in user's org greenhouses."""
    if not current_user.organization_id:
        return []

    gh_ids = [
        g.id
        for g in db.query(Greenhouse.id)
        .filter(Greenhouse.organization_id == current_user.organization_id)
        .all()
    ]
    if not gh_ids:
        return []

    devices = db.query(Device).filter(Device.greenhouse_id.in_(gh_ids)).all()
    results = []
    for dev in devices:
        sensor_count = db.query(func.count(Sensor.id)).filter(Sensor.device_id == dev.id).scalar()
        gh = db.query(Greenhouse).filter(Greenhouse.id == dev.greenhouse_id).first()
        results.append(
            DeviceResponse(
                id=str(dev.id),
                serial=dev.serial,
                name=dev.name,
                type=dev.type,
                fw_version=dev.fw_version,
                status=dev.status,
                last_seen=dev.last_seen.isoformat() if dev.last_seen else None,
                greenhouse_id=str(dev.greenhouse_id) if dev.greenhouse_id else None,
                greenhouse_name=gh.name if gh else None,
                sensor_count=sensor_count,
                paired_at=dev.paired_at.isoformat() if dev.paired_at else None,
            )
        )
    return results


@router.post("/pairing-code", response_model=PairingCodeResponse, status_code=201)
async def generate_pairing_code(
    data: PairingCodeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Generate a short-lived pairing code for a greenhouse."""
    if not current_user.organization_id:
        raise HTTPException(status_code=400, detail="User must belong to an organization")

    # Verify greenhouse belongs to user's org
    gh = (
        db.query(Greenhouse)
        .filter(
            Greenhouse.id == data.greenhouse_id,
            Greenhouse.organization_id == current_user.organization_id,
        )
        .first()
    )
    if not gh:
        raise HTTPException(status_code=404, detail="Greenhouse not found")

    # Generate unique code
    chars = string.ascii_uppercase + string.digits
    for _ in range(10):
        code = "".join(secrets.choice(chars) for _ in range(PAIRING_CODE_LENGTH))
        existing = (
            db.query(PairingCode)
            .filter(PairingCode.code == code, PairingCode.used_at.is_(None))
            .first()
        )
        if not existing:
            break
    else:
        raise HTTPException(status_code=500, detail="Could not generate unique code")

    expires_at = datetime.now(UTC) + timedelta(minutes=PAIRING_CODE_EXPIRY_MINUTES)
    pc = PairingCode(
        code=code,
        greenhouse_id=gh.id,
        expires_at=expires_at,
    )
    db.add(pc)
    db.commit()
    db.refresh(pc)

    return PairingCodeResponse(
        code=pc.code,
        expires_at=pc.expires_at.isoformat(),
        greenhouse_id=str(pc.greenhouse_id),
    )


@router.post("/pair", response_model=PairDeviceResponse, status_code=201)
async def pair_device(
    data: PairDeviceRequest,
    db: Session = Depends(get_db),
):
    """Device submits pairing code to register itself. Returns an API key for future data ingest."""
    now = datetime.now(UTC)

    pc = (
        db.query(PairingCode)
        .filter(
            PairingCode.code == data.code.upper(),
            PairingCode.used_at.is_(None),
            PairingCode.expires_at > now,
        )
        .first()
    )
    if not pc:
        raise HTTPException(status_code=400, detail="Invalid or expired pairing code")

    # Check if serial already registered
    existing = db.query(Device).filter(Device.serial == data.serial).first()
    if existing:
        raise HTTPException(status_code=409, detail="Device serial already registered")

    # Generate API key for the device
    api_key = secrets.token_urlsafe(32)

    device = Device(
        greenhouse_id=pc.greenhouse_id,
        serial=data.serial,
        name=data.name or data.serial,
        type=data.type,
        fw_version=data.fw_version,
        status="online",
        api_key_hash=get_password_hash(api_key),
        paired_at=now,
        last_seen=now,
    )
    db.add(device)
    db.flush()

    pc.used_at = now
    pc.device_id = device.id
    db.commit()
    db.refresh(device)

    return PairDeviceResponse(
        device_id=str(device.id),
        api_key=api_key,
        greenhouse_id=str(device.greenhouse_id),
    )
