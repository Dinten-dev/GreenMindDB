"""Business logic for gateways and pairing."""

import secrets
import string
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import get_password_hash
from app.models.master import Gateway, Greenhouse, Sensor
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


def _require_org(user: User):
    if not user.organization_id:
        raise HTTPException(status_code=400, detail="User must belong to an organization")
    return user.organization_id


def list_gateways(
    db: Session, user: User, *, greenhouse_id: str | None = None
) -> list[GatewayResponse]:
    org_id = _require_org(user)

    gh_ids = [
        g.id for g in db.query(Greenhouse.id).filter(Greenhouse.organization_id == org_id).all()
    ]
    if not gh_ids:
        return []

    query = db.query(Gateway).filter(Gateway.greenhouse_id.in_(gh_ids))
    if greenhouse_id:
        query = query.filter(Gateway.greenhouse_id == greenhouse_id)

    gateways = query.all()
    results = []
    for gw in gateways:
        sensor_count = db.query(func.count(Sensor.id)).filter(Sensor.gateway_id == gw.id).scalar()
        gh = db.query(Greenhouse).filter(Greenhouse.id == gw.greenhouse_id).first()
        results.append(
            GatewayResponse(
                id=str(gw.id),
                greenhouse_id=str(gw.greenhouse_id),
                greenhouse_name=gh.name if gh else None,
                hardware_id=gw.hardware_id,
                name=gw.name,
                local_ip=gw.local_ip,
                fw_version=gw.fw_version,
                status=gw.status,
                is_active=gw.is_active,
                last_seen=gw.last_seen.isoformat() if gw.last_seen else None,
                paired_at=gw.paired_at.isoformat() if gw.paired_at else None,
                sensor_count=sensor_count,
            )
        )
    return results


def generate_pairing_code(db: Session, user: User, greenhouse_id: str) -> PairingCodeResponse:
    org_id = _require_org(user)

    gh = (
        db.query(Greenhouse)
        .filter(Greenhouse.id == greenhouse_id, Greenhouse.organization_id == org_id)
        .first()
    )
    if not gh:
        raise HTTPException(status_code=404, detail="Greenhouse not found")

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
        code=code, greenhouse_id=gh.id, expires_at=expires_at, created_by_user_id=user.id
    )
    db.add(pc)
    db.commit()
    db.refresh(pc)

    return PairingCodeResponse(
        code=pc.code,
        expires_at=pc.expires_at.isoformat(),
        greenhouse_id=str(pc.greenhouse_id),
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
        greenhouse_id=pc.greenhouse_id,
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
        greenhouse_id=str(gateway.greenhouse_id),
    )


def delete_gateway(db: Session, user: User, gateway_id: str) -> None:
    org_id = _require_org(user)

    gateway = (
        db.query(Gateway)
        .join(Greenhouse)
        .filter(Gateway.id == gateway_id, Greenhouse.organization_id == org_id)
        .first()
    )
    if not gateway:
        raise HTTPException(status_code=404, detail="Gateway not found")

    db.delete(gateway)
    db.commit()
