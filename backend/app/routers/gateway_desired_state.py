"""Gateway-facing endpoints for the update agent.

All endpoints authenticate via X-Api-Key header (per-gateway bcrypt key).
"""

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.file_security import resolve_contained_path, verify_signed_file
from app.gateway_auth import get_current_gateway
from app.models.gateway_remote import (
    GatewayAppRelease,
    GatewayConfigRelease,
    GatewayDesiredState,
)
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


@router.get("/desired-state", response_model=DesiredStateResponse)
async def handle_desired_state(
    current_app_version: str | None = Query(None, max_length=50),
    current_config_version: str | None = Query(None, max_length=50),
    current_agent_version: str | None = Query(None, max_length=50),
    gateway: Gateway = Depends(get_current_gateway),
    db: Session = Depends(get_db),
):
    """Agent polls this endpoint to get the desired target state."""
    return get_desired_state(
        db,
        gateway,
        current_app_version=current_app_version,
        current_config_version=current_config_version,
    )


@router.post("/state-report", status_code=200)
async def handle_state_report(
    data: StateReportRequest,
    gateway: Gateway = Depends(get_current_gateway),
    db: Session = Depends(get_db),
):
    """Agent reports its current state, versions, and health."""
    if data.gateway_id != gateway.id:
        raise HTTPException(status_code=403, detail="Gateway ID mismatch")

    process_state_report(db, gateway, data.model_dump())
    return {"status": "ok"}


@router.post("/command-result", status_code=200)
async def handle_command_result(
    data: CommandResultRequest,
    gateway: Gateway = Depends(get_current_gateway),
    db: Session = Depends(get_db),
):
    """Agent reports the result of a remote command execution."""
    if data.gateway_id != gateway.id:
        raise HTTPException(status_code=403, detail="Gateway ID mismatch")
    process_command_result(db, gateway, data.command_id, data.result, data.message)
    return {"status": "ok"}


@router.get("/app-release/{version}/download")
async def handle_app_release_download(
    version: str = Path(
        max_length=50,
        pattern=(
            r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
            r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
            r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
        ),
    ),
    gateway: Gateway = Depends(get_current_gateway),
    db: Session = Depends(get_db),
):
    """Download a gateway app release tarball."""
    release = (
        db.query(GatewayAppRelease)
        .join(
            GatewayDesiredState,
            GatewayDesiredState.desired_app_version == GatewayAppRelease.version,
        )
        .filter(GatewayAppRelease.version == version, GatewayAppRelease.is_active.is_(True))
        .filter(GatewayDesiredState.gateway_id == gateway.id)
        .first()
    )
    if not release:
        raise HTTPException(status_code=404, detail="Release not found or inactive")

    try:
        file_path = resolve_contained_path(GATEWAY_RELEASE_DIR, release.artifact_path)
        if not file_path.is_file():
            raise ValueError("Release artifact is missing")
        verify_signed_file(
            file_path,
            release.sha256,
            release.signature,
            settings.gateway_release_signing_public_key_path,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Release artifact verification failed") from exc

    return FileResponse(
        path=file_path,
        media_type="application/gzip",
        filename=file_path.name,
        headers={"X-Checksum-SHA256": release.sha256},
    )


@router.get("/config-release/{version}/download")
async def handle_config_release_download(
    version: str = Path(max_length=50, pattern=r"^[A-Za-z0-9._+-]+$"),
    gateway: Gateway = Depends(get_current_gateway),
    db: Session = Depends(get_db),
):
    """Download a gateway config release as JSON."""
    config = (
        db.query(GatewayConfigRelease)
        .join(
            GatewayDesiredState,
            GatewayDesiredState.desired_config_version == GatewayConfigRelease.version,
        )
        .filter(GatewayConfigRelease.version == version, GatewayConfigRelease.is_active.is_(True))
        .filter(GatewayDesiredState.gateway_id == gateway.id)
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
