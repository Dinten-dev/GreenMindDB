"""Business logic for gateways and pairing."""

import secrets
import string
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import get_password_hash
from app.models.master import Gateway, Sensor, Zone
from app.models.pairing import PairingCode
from app.models.user import User
from app.schemas.gateway import (
    GatewayResponse,
    PairingCodeResponse,
    RegisterGatewayRequest,
    RegisterGatewayResponse,
)

PAIRING_CODE_LENGTH = 6
PAIRING_CODE_EXPIRY_MINUTES = 10
LIVENESS_THRESHOLD = timedelta(minutes=5)


def _require_org(user: User):
    if not user.organization_id:
        raise HTTPException(status_code=400, detail="User must belong to an organization")
    return user.organization_id


def list_gateways(
    db: Session, user: User, *, zone_id: str | None = None
) -> list[GatewayResponse]:
    org_id = _require_org(user)

    z_ids = [
        z.id for z in db.query(Zone.id).filter(Zone.organization_id == org_id).all()
    ]
    if not z_ids:
        return []

    query = db.query(Gateway).filter(Gateway.zone_id.in_(z_ids))
    if zone_id:
        query = query.filter(Gateway.zone_id == zone_id)

    gateways = query.all()
    now = datetime.now(UTC)
    results = []
    for gw in gateways:
        sensor_count = db.query(func.count(Sensor.id)).filter(Sensor.gateway_id == gw.id).scalar()
        z = db.query(Zone).filter(Zone.id == gw.zone_id).first()
        is_online = bool(gw.last_seen and (now - gw.last_seen) < LIVENESS_THRESHOLD)
        results.append(
            GatewayResponse(
                id=str(gw.id),
                zone_id=str(gw.zone_id),
                zone_name=z.name if z else None,
                hardware_id=gw.hardware_id,
                name=gw.name,
                local_ip=gw.local_ip,
                fw_version=gw.fw_version,
                status="online" if is_online else "offline",
                is_active=gw.is_active,
                last_seen=gw.last_seen.isoformat() if gw.last_seen else None,
                paired_at=gw.paired_at.isoformat() if gw.paired_at else None,
                sensor_count=sensor_count,
            )
        )
    return results


def generate_pairing_code(db: Session, user: User, zone_id: str) -> PairingCodeResponse:
    org_id = _require_org(user)

    z = (
        db.query(Zone)
        .filter(Zone.id == zone_id, Zone.organization_id == org_id)
        .first()
    )
    if not z:
        raise HTTPException(status_code=404, detail="Zone not found")

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
        code=code, zone_id=z.id, expires_at=expires_at, created_by_user_id=user.id
    )
    db.add(pc)
    db.commit()
    db.refresh(pc)

    return PairingCodeResponse(
        code=pc.code,
        expires_at=pc.expires_at.isoformat(),
        zone_id=str(pc.zone_id),
    )


def register_gateway(db: Session, data: RegisterGatewayRequest) -> RegisterGatewayResponse:
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

    existing = db.query(Gateway).filter(Gateway.hardware_id == data.hardware_id).first()
    if existing:
        raise HTTPException(status_code=409, detail="Gateway hardware_id already registered")

    api_key = secrets.token_urlsafe(32)

    gateway = Gateway(
        zone_id=pc.zone_id,
        hardware_id=data.hardware_id,
        name=data.name or data.hardware_id,
        fw_version=data.fw_version,
        local_ip=data.local_ip,
        status="online",
        api_key_hash=get_password_hash(api_key),
        paired_at=now,
        last_seen=now,
    )
    db.add(gateway)
    db.flush()

    pc.used_at = now
    pc.gateway_id = gateway.id
    db.commit()
    db.refresh(gateway)

    return RegisterGatewayResponse(
        gateway_id=str(gateway.id),
        api_key=api_key,
        zone_id=str(gateway.zone_id),
    )


def delete_gateway(db: Session, user: User, gateway_id: str) -> None:
    org_id = _require_org(user)

    gateway = (
        db.query(Gateway)
        .join(Zone)
        .filter(Gateway.id == gateway_id, Zone.organization_id == org_id)
        .first()
    )
    if not gateway:
        raise HTTPException(status_code=404, detail="Gateway not found")

    db.delete(gateway)
    db.commit()
