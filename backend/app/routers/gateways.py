"""Gateway management endpoints."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_role
from app.database import get_db
from app.gateway_auth import get_current_gateway
from app.models.master import Gateway
from app.models.user import Role, User
from app.rate_limit import limiter
from app.schemas.gateway import (
    GatewayDiscoveryRequest,
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

router = APIRouter(prefix="/gateways", tags=["gateways"])
_tenant_manager = require_role([Role.OWNER, Role.ADMIN])


@router.get("", response_model=list[GatewayResponse])
async def handle_list_gateways(
    zone_id: uuid.UUID | None = None,
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
    current_user: User = Depends(_tenant_manager),
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
    x_api_key: str | None = Header(None, alias="X-Api-Key"),
):
    """Gateway submits pairing code + hardware_id to register. Returns API key."""
    return register_gateway(db, data, current_api_key=x_api_key)


@router.post("/heartbeat", status_code=200)
async def handle_heartbeat(
    data: HeartbeatRequest,
    gateway: Gateway = Depends(get_current_gateway),
    db: Session = Depends(get_db),
):
    """Gateway sends a heartbeat to update last_seen and local_ip."""
    if gateway.hardware_id != data.hardware_id:
        raise HTTPException(status_code=403, detail="Gateway identity mismatch")

    gateway.last_seen = datetime.now(UTC)
    gateway.status = "online"
    if data.local_ip:
        gateway.local_ip = data.local_ip
    gateway.wav_pending_files = data.wav_pending_files
    gateway.wav_pending_bytes = data.wav_pending_bytes
    gateway.wav_oldest_pending_age_hours = data.wav_oldest_pending_age_hours
    gateway.wav_last_upload_at = data.wav_last_upload_at
    gateway.wav_last_error_code = data.wav_last_error_code
    db.commit()

    return {"status": "ok"}


@router.delete("/{gateway_id}", status_code=204)
async def handle_delete_gateway(
    gateway_id: uuid.UUID,
    current_user: User = Depends(_tenant_manager),
    db: Session = Depends(get_db),
):
    """Delete a gateway and invalidate its API key."""
    delete_gateway(db, current_user, gateway_id)


@router.post("/{gateway_id}/sensors/register", status_code=201)
async def handle_sensor_register(
    gateway_id: uuid.UUID,
    data: GatewayDiscoveryRequest,
    gateway: Gateway = Depends(get_current_gateway),
    db: Session = Depends(get_db),
):
    """Gateway registers a sensor utilizing a pairing code."""
    if gateway.id != gateway_id:
        raise HTTPException(status_code=403, detail="Gateway ID mismatch")

    return register_sensor(db, gateway, data.mac_address, data.code)


@router.get("/{gateway_id}/commands", response_model=list[dict])
async def handle_get_commands(
    gateway_id: uuid.UUID,
    gateway: Gateway = Depends(get_current_gateway),
    db: Session = Depends(get_db),
):
    """Gateway pulls pending commands."""
    if gateway.id != gateway_id:
        raise HTTPException(status_code=403, detail="Gateway ID mismatch")
    return pull_gateway_commands(db, gateway)
