"""Gateway-facing endpoints for the update agent.

All endpoints authenticate via X-Api-Key header (per-gateway bcrypt key).
"""

import os

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.auth import verify_password
from app.database import get_db
from app.models.gateway_remote import GatewayAppRelease, GatewayConfigRelease
from app.models.master import Gateway
from app.schemas.gateway_remote import (
    CommandResultRequest,
    DesiredStateResponse,
    StateReportRequest,
)
from app.services.gateway_remote_service import (
    GATEWAY_RELEASE_DIR,
    get_desired_state,
    process_command_result,
    process_state_report,
)

router = APIRouter(prefix="/gateway", tags=["gateway-agent"])


def _auth_gateway_by_key(db: Session, api_key: str) -> Gateway:
    """Authenticate a gateway by its API key."""
    gateways = db.query(Gateway).filter(Gateway.is_active.is_(True)).all()
    for gw in gateways:
        if gw.api_key_hash and verify_password(api_key, gw.api_key_hash):
            return gw
    raise HTTPException(status_code=401, detail="Invalid API key")


@router.get("/desired-state", response_model=DesiredStateResponse)
async def handle_desired_state(
    current_app_version: str | None = None,
    current_config_version: str | None = None,
    current_agent_version: str | None = None,
    db: Session = Depends(get_db),
    x_api_key: str | None = Header(None),
):
    """Agent polls this endpoint to get the desired target state."""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-Api-Key header")
    gateway = _auth_gateway_by_key(db, x_api_key)

    return get_desired_state(
        db,
        gateway,
        current_app_version=current_app_version,
        current_config_version=current_config_version,
    )


@router.post("/state-report", status_code=200)
async def handle_state_report(
    data: StateReportRequest,
    db: Session = Depends(get_db),
    x_api_key: str | None = Header(None),
):
    """Agent reports its current state, versions, and health."""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-Api-Key header")
    gateway = _auth_gateway_by_key(db, x_api_key)

    process_state_report(db, gateway, data.model_dump())
    return {"status": "ok"}


@router.post("/command-result", status_code=200)
async def handle_command_result(
    data: CommandResultRequest,
    db: Session = Depends(get_db),
    x_api_key: str | None = Header(None),
):
    """Agent reports the result of a remote command execution."""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-Api-Key header")
    _auth_gateway_by_key(db, x_api_key)

    process_command_result(db, None, data.command_id, data.result, data.message)
    return {"status": "ok"}


@router.get("/app-release/{version}/download")
async def handle_app_release_download(
    version: str,
    db: Session = Depends(get_db),
    x_api_key: str | None = Header(None),
):
    """Download a gateway app release tarball."""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-Api-Key header")
    _auth_gateway_by_key(db, x_api_key)

    release = (
        db.query(GatewayAppRelease)
        .filter(GatewayAppRelease.version == version, GatewayAppRelease.is_active.is_(True))
        .first()
    )
    if not release:
        raise HTTPException(status_code=404, detail="Release not found or inactive")

    file_path = os.path.join(GATEWAY_RELEASE_DIR, release.artifact_path)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Release artifact missing on disk")

    return FileResponse(
        path=file_path,
        media_type="application/gzip",
        filename=release.artifact_path,
        headers={"X-Checksum-SHA256": release.sha256},
    )


@router.get("/config-release/{version}/download")
async def handle_config_release_download(
    version: str,
    db: Session = Depends(get_db),
    x_api_key: str | None = Header(None),
):
    """Download a gateway config release as JSON."""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-Api-Key header")
    _auth_gateway_by_key(db, x_api_key)

    config = (
        db.query(GatewayConfigRelease)
        .filter(GatewayConfigRelease.version == version, GatewayConfigRelease.is_active.is_(True))
        .first()
    )
    if not config:
        raise HTTPException(status_code=404, detail="Config release not found or inactive")

    return {
        "version": config.version,
        "config_payload": config.config_payload,
        "sha256": config.sha256,
        "schema_version": config.schema_version,
    }
