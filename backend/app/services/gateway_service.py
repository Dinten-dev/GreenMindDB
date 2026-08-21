"""Business logic for gateways and pairing."""

import logging
import secrets
import string
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.gateway_auth import (
    generate_gateway_api_key,
    set_gateway_api_key,
    verify_gateway_api_key,
)
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

logger = logging.getLogger(__name__)

# Transient in-memory state for ESP32 Handshake (MVP)
# MAC -> {"code": "...", "gateway_id": "...", "expires_at": datetime}
discovered_sensors_cache = {}

# gateway_id -> [ {"action": "...", "mac_address": "..."} ]
gateway_commands_cache = {}


def _require_org(user: User):
    if not user.organization_id:
        raise HTTPException(status_code=400, detail="User must belong to an organization")
    return user.organization_id


def list_gateways(
    db: Session, user: User, *, zone_id: uuid.UUID | None = None
) -> list[GatewayResponse]:
    org_id = _require_org(user)

    z_ids = [z.id for z in db.query(Zone.id).filter(Zone.organization_id == org_id).all()]
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
        last_seen = gw.last_seen
        if last_seen and last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=UTC)
        is_online = bool(last_seen and (now - last_seen) < LIVENESS_THRESHOLD)
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


def generate_pairing_code(db: Session, user: User, zone_id: uuid.UUID | str) -> PairingCodeResponse:
    org_id = _require_org(user)

    z = db.query(Zone).filter(Zone.id == zone_id, Zone.organization_id == org_id).first()
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
    pc = PairingCode(code=code, zone_id=z.id, expires_at=expires_at, created_by_user_id=user.id)
    db.add(pc)
    db.commit()
    db.refresh(pc)

    return PairingCodeResponse(
        code=pc.code,
        expires_at=pc.expires_at.isoformat(),
        zone_id=str(pc.zone_id),
    )


def register_gateway(
    db: Session,
    data: RegisterGatewayRequest,
    current_api_key: str | None = None,
) -> RegisterGatewayResponse:
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
        if not existing.is_active:
            raise HTTPException(status_code=403, detail="Gateway deactivated")
        if not verify_gateway_api_key(existing, current_api_key):
            raise HTTPException(
                status_code=409,
                detail="Existing gateway must prove possession of its current API key",
            )

        current_zone = db.query(Zone).filter(Zone.id == existing.zone_id).first()
        requested_zone = db.query(Zone).filter(Zone.id == pc.zone_id).first()
        if (
            not current_zone
            or not requested_zone
            or current_zone.organization_id != requested_zone.organization_id
        ):
            raise HTTPException(
                status_code=409,
                detail="Gateway reassignment across organizations is not allowed",
            )

        # Same-tenant re-provisioning rotates the credential and keeps sensor identity.
        existing.zone_id = pc.zone_id
        existing.name = data.name or existing.name
        existing.fw_version = data.fw_version or existing.fw_version
        existing.local_ip = data.local_ip
        existing.status = "online"
        existing.paired_at = now
        existing.last_seen = now
        gateway = existing
        logger.info(
            "Re-provisioned gateway %s (hw: %s) to zone %s",
            gateway.id,
            data.hardware_id,
            pc.zone_id,
        )
    else:
        gateway = Gateway(
            zone_id=pc.zone_id,
            hardware_id=data.hardware_id,
            name=data.name or data.hardware_id,
            fw_version=data.fw_version,
            local_ip=data.local_ip,
            status="online",
            paired_at=now,
            last_seen=now,
        )
        db.add(gateway)
    db.flush()

    api_key = generate_gateway_api_key(gateway.id)
    set_gateway_api_key(gateway, api_key)

    pc.used_at = now
    pc.gateway_id = gateway.id
    db.commit()
    db.refresh(gateway)

    return RegisterGatewayResponse(
        gateway_id=str(gateway.id),
        api_key=api_key,
        zone_id=str(gateway.zone_id),
    )


def delete_gateway(db: Session, user: User, gateway_id: uuid.UUID | str) -> None:
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


# --- Sensor Pairing Workflow ---


def register_sensor(db: Session, gw: Gateway, mac_address: str, code: str) -> dict:
    """Gateway forwards the sensor's pairing code to register it to its zone."""
    now = datetime.now(UTC)

    pc = (
        db.query(PairingCode)
        .filter(
            PairingCode.code == code.upper(),
            PairingCode.used_at.is_(None),
            PairingCode.expires_at > now,
        )
        .first()
    )
    if not pc:
        raise HTTPException(status_code=400, detail="Invalid or expired pairing code")

    if pc.zone_id != gw.zone_id:
        raise HTTPException(status_code=400, detail="Pairing code zone does not match Gateway zone")

    existing = db.query(Sensor).filter(Sensor.mac_address == mac_address).first()
    if existing:
        if existing.gateway_id != gw.id:
            raise HTTPException(status_code=409, detail="Sensor is already assigned")
        sensor = existing
        sensor.status = "online"
        sensor.last_seen = now
    else:
        sensor = Sensor(
            gateway_id=gw.id,
            mac_address=mac_address,
            name=f"Sensor-{mac_address[-4:]}",
            sensor_type="generic",
            status="online",
            last_seen=now,
        )
        db.add(sensor)

    pc.used_at = now
    db.commit()
    db.refresh(sensor)

    return {"status": "ok", "sensor_id": str(sensor.id)}


def pull_gateway_commands(db: Session, gateway: Gateway) -> list[dict]:
    cmds = gateway_commands_cache.pop(str(gateway.id), [])
    return cmds
