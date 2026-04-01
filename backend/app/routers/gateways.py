"""Gateway management endpoints."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth import get_current_user, verify_password
from app.database import get_db
from app.models.master import Gateway
from app.models.user import User
from app.rate_limit import limiter
from app.schemas.gateway import (
    GatewayResponse,
    HeartbeatRequest,
    PairingCodeRequest,
    PairingCodeResponse,
    RegisterGatewayRequest,
    RegisterGatewayResponse,
)
from app.services.gateway_service import (
    delete_gateway,
    generate_pairing_code,
    list_gateways,
    pull_gateway_commands,
    register_gateway,
    register_sensor,
)


def _auth_gateway(db: Session, api_key: str):
    gateways = db.query(Gateway).filter(Gateway.is_active.is_(True)).all()
    for gw in gateways:
        if gw.api_key_hash and verify_password(api_key, gw.api_key_hash):
            return gw
    raise HTTPException(status_code=401, detail="Invalid API key")


router = APIRouter(prefix="/gateways", tags=["gateways"])


@router.get("", response_model=list[GatewayResponse])
async def handle_list_gateways(
    zone_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List gateways. Optionally filter by zone_id."""
    return list_gateways(db, current_user, zone_id=zone_id)


@router.post("/pairing-code", response_model=PairingCodeResponse, status_code=201)
@limiter.limit("5/minute")
async def handle_generate_pairing_code(
    request: Request,
    data: PairingCodeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Generate a short-lived pairing code for a zone."""
    return generate_pairing_code(db, current_user, data.zone_id)


@router.post("/register", response_model=RegisterGatewayResponse, status_code=201)
@limiter.limit("5/minute")
async def handle_register_gateway(
    request: Request,
    data: RegisterGatewayRequest,
    db: Session = Depends(get_db),
):
    """Gateway submits pairing code + hardware_id to register. Returns API key."""
    return register_gateway(db, data)


@router.post("/heartbeat", status_code=200)
async def handle_heartbeat(
    data: HeartbeatRequest,
    db: Session = Depends(get_db),
    x_api_key: str | None = Header(None),
):
    """Gateway sends a heartbeat to update last_seen and local_ip."""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-Api-Key header")

    gateway = db.query(Gateway).filter(Gateway.hardware_id == data.hardware_id).first()
    if not gateway:
        raise HTTPException(
            status_code=410,
            detail={"action": "RESET_TO_SETUP_MODE", "reason": "unassigned"}
        )
    if not gateway.is_active:
        raise HTTPException(status_code=403, detail="Gateway deactivated")
    if not gateway.api_key_hash or not verify_password(x_api_key, gateway.api_key_hash):
        raise HTTPException(status_code=401, detail="Invalid API key")

    gateway.last_seen = datetime.now(UTC)
    gateway.status = "online"
    if data.local_ip:
        gateway.local_ip = data.local_ip
    db.commit()

    return {"status": "ok"}


@router.delete("/{gateway_id}", status_code=204)
async def handle_delete_gateway(
    gateway_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a gateway and invalidate its API key."""
    delete_gateway(db, current_user, gateway_id)

@router.post("/{gateway_id}/sensors/register", status_code=201)
async def handle_sensor_register(
    gateway_id: str,
    data: dict,
    db: Session = Depends(get_db),
    x_api_key: str | None = Header(None),
):
    """Gateway registers a sensor utilizing a pairing code."""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing API Key")
    gw = _auth_gateway(db, x_api_key)

    if str(gw.id) != gateway_id:
        raise HTTPException(status_code=403, detail="Gateway ID mismatch")

    return register_sensor(db, gw, data.get("mac_address", ""), data.get("code", ""))

@router.get("/{gateway_id}/commands", response_model=list[dict])
async def handle_get_commands(
    gateway_id: str,
    db: Session = Depends(get_db),
    x_api_key: str | None = Header(None),
):
    """Gateway pulls pending commands."""
    if not x_api_key:
        raise HTTPException(status_code=401)
    _auth_gateway(db, x_api_key)
    return pull_gateway_commands(db, gateway_id)

