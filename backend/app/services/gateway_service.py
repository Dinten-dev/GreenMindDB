"""Business logic for gateways and pairing."""

import logging
import secrets
import string
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.gateway_auth import (
    generate_gateway_api_key,
    set_gateway_api_key,
    verify_gateway_api_key,
)
from app.models.master import Gateway, Sensor, Zone
from app.models.pairing import PairingCode, PairingPurpose
from app.models.provisioning import ProvisioningJob
from app.models.user import User
from app.schemas.gateway import (
    GatewayResponse,
    PairingCodeResponse,
    RegisterGatewayRequest,
    RegisterGatewayResponse,
)
from app.zone_access import zone_access_filter

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
    _require_org(user)

    z_ids = [z.id for z in db.query(Zone.id).filter(zone_access_filter(user)).all()]
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
                wav_pending_files=gw.wav_pending_files,
                wav_pending_bytes=gw.wav_pending_bytes,
                wav_oldest_pending_age_hours=gw.wav_oldest_pending_age_hours,
                wav_last_upload_at=(
                    gw.wav_last_upload_at.isoformat() if gw.wav_last_upload_at else None
                ),
                wav_last_error_code=gw.wav_last_error_code,
            )
        )
    return results


def generate_pairing_code(
    db: Session, user: User, zone_id: uuid.UUID | str, *, purpose: PairingPurpose
) -> PairingCodeResponse:
    _require_org(user)
    if purpose not in (PairingPurpose.GATEWAY, PairingPurpose.SENSOR):
        raise ValueError("Only typed pairing codes may be issued")
    z = db.query(Zone).filter(Zone.id == zone_id, zone_access_filter(user)).first()
    if not z:
        raise HTTPException(status_code=404, detail="Zone not found")

    chars = string.ascii_uppercase + string.digits
    for _ in range(10):
        code = "".join(secrets.choice(chars) for _ in range(PAIRING_CODE_LENGTH))
        # Retention can remove a code before its provisioning job. Do not bind
        # that old job (including its credentials) to a newly issued code.
        if db.query(PairingCode.id).filter(PairingCode.code == code).first():
            continue
        if db.query(ProvisioningJob.id).filter(ProvisioningJob.pairing_code == code).first():
            continue
        pc = PairingCode(
            code=code,
            purpose=purpose.value,
            zone_id=z.id,
            expires_at=datetime.now(UTC) + timedelta(minutes=PAIRING_CODE_EXPIRY_MINUTES),
            created_by_user_id=user.id,
        )
        try:
            with db.begin_nested():
                db.add(pc)
                db.flush()
        except IntegrityError:
            # A concurrent issuer may have chosen the same code.
            continue
        db.commit()
        return PairingCodeResponse(
            code=pc.code,
            purpose=pc.purpose,
            expires_at=pc.expires_at.isoformat(),
            zone_id=str(pc.zone_id),
        )
    db.rollback()
    raise HTTPException(status_code=503, detail="Could not generate unique code")


@contextmanager
def _pairing_transaction(db: Session):
    try:
        yield
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Device identity already assigned") from exc
    except Exception:
        db.rollback()
        raise


def _locked_pairing_code(db: Session, code: str, purpose: PairingPurpose):
    # Lock by unique code before checking state. PostgreSQL rechecks the row
    # after a concurrent redemption; populate_existing discards stale ORM state.
    pc = (
        db.query(PairingCode)
        .filter(PairingCode.code == code.upper())
        .populate_existing()
        .with_for_update()
        .first()
    )
    return pc, _pairing_code_time(pc, purpose)


def _pairing_code_time(pc: PairingCode | None, purpose: PairingPurpose):
    now = datetime.now(UTC)  # Codes may expire while waiting for any device lock.
    expiry = pc.expires_at if pc else None
    if expiry is not None and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    if not pc or pc.purpose != purpose.value or pc.used_at or expiry <= now:
        raise HTTPException(status_code=400, detail="Invalid or expired pairing code")
    return now


def register_gateway(
    db: Session,
    data: RegisterGatewayRequest,
    current_api_key: str | None = None,
) -> RegisterGatewayResponse:
    with _pairing_transaction(db):
        pc, now = _locked_pairing_code(db, data.code, PairingPurpose.GATEWAY)
        existing = (
            db.query(Gateway)
            .filter(Gateway.hardware_id == data.hardware_id)
            .populate_existing()
            .with_for_update()
            .first()
        )
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

        pc.used_at = _pairing_code_time(pc, PairingPurpose.GATEWAY)
        pc.gateway_id = gateway.id

        return RegisterGatewayResponse(
            gateway_id=str(gateway.id),
            api_key=api_key,
            zone_id=str(gateway.zone_id),
        )


def delete_gateway(db: Session, user: User, gateway_id: uuid.UUID | str) -> None:
    _require_org(user)

    gateway = (
        db.query(Gateway)
        .join(Zone)
        .filter(Gateway.id == gateway_id, zone_access_filter(user))
        .first()
    )
    if not gateway:
        raise HTTPException(status_code=404, detail="Gateway not found")

    db.delete(gateway)
    db.commit()


# --- Sensor Pairing Workflow ---


def register_sensor(
    db: Session, gw: Gateway, mac_address: str, code: str, current_api_key: str | None = None
) -> dict:
    """Gateway forwards the sensor's pairing code to register it to its zone."""
    authenticated_hash = gw.api_key_hash
    with _pairing_transaction(db):
        pc, now = _locked_pairing_code(db, code, PairingPurpose.SENSOR)
        # Authentication happened before the code lock. Refresh zone/active state
        # before binding a sensor, including a concurrent Gateway reassignment.
        gateway = (
            db.query(Gateway)
            .filter(Gateway.id == gw.id)
            .populate_existing()
            .with_for_update()
            .first()
        )
        if not gateway or not gateway.is_active:
            raise HTTPException(status_code=403, detail="Gateway deactivated")
        if gateway.api_key_hash != authenticated_hash and not verify_gateway_api_key(
            gateway, current_api_key
        ):
            raise HTTPException(status_code=401, detail="Gateway credential changed")
        if pc.zone_id != gateway.zone_id:
            raise HTTPException(
                status_code=400, detail="Pairing code zone does not match Gateway zone"
            )

        existing = (
            db.query(Sensor)
            .filter(Sensor.mac_address == mac_address)
            .populate_existing()
            .with_for_update()
            .first()
        )
        if existing:
            if existing.gateway_id != gateway.id:
                raise HTTPException(status_code=409, detail="Sensor is already assigned")
            sensor = existing
            sensor.status = "online"
            sensor.last_seen = now
        else:
            sensor = Sensor(
                gateway_id=gateway.id,
                mac_address=mac_address,
                name=f"Sensor-{mac_address[-4:]}",
                sensor_type="generic",
                status="online",
                last_seen=now,
            )
            db.add(sensor)
        db.flush()
        pc.used_at = _pairing_code_time(pc, PairingPurpose.SENSOR)
        pc.gateway_id = gateway.id
        return {"status": "ok", "sensor_id": str(sensor.id)}


def pull_gateway_commands(db: Session, gateway: Gateway) -> list[dict]:
    cmds = gateway_commands_cache.pop(str(gateway.id), [])
    return cmds
