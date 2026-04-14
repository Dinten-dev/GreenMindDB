"""Admin endpoints for gateway remote management.

All endpoints require JWT authentication and ADMIN or OWNER role.
"""

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_role
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.gateway_remote import (
    GatewayAppRelease,
    GatewayCommand,
    GatewayConfigRelease,
    GatewayDesiredState,
)
from app.models.user import Role, User
from app.schemas.gateway_remote import (
    AppReleaseListResponse,
    AppReleaseResponse,
    CommandCreate,
    CommandListResponse,
    CommandResponse,
    ConfigReleaseCreate,
    ConfigReleaseListResponse,
    ConfigReleaseResponse,
    DesiredStateUpdate,
    GatewayFleetResponse,
    RollbackRequest,
    RolloutCreate,
    UpdateLogListResponse,
    UpdateLogResponse,
)
from app.services.gateway_remote_service import (
    delete_app_release,
    get_fleet_overview,
    initiate_rollback,
    initiate_rollout,
    issue_command,
    list_app_releases,
    list_config_releases,
    list_update_logs,
    set_desired_state,
    toggle_app_release,
    toggle_config_release,
    upload_app_release,
    upload_config_release,
)

router = APIRouter(prefix="/admin", tags=["gateway-admin"])

_admin = require_role([Role.ADMIN, Role.OWNER])


# ── Fleet Overview ───────────────────────────────────────────────────


@router.get("/gateway-fleet", response_model=GatewayFleetResponse)
async def handle_fleet_overview(
    zone_id: str | None = None,
    status: str | None = None,
    offset: int = 0,
    limit: int = 50,
    user: User = Depends(_admin),
    db: Session = Depends(get_db),
):
    """List all gateways with their current state, versions, and health."""
    import uuid as _uuid

    z_id = _uuid.UUID(zone_id) if zone_id else None
    items, total = get_fleet_overview(
        db, zone_id=z_id, status_filter=status, offset=offset, limit=limit
    )
    return GatewayFleetResponse(items=items, total=total)


# ── App Releases ─────────────────────────────────────────────────────


@router.post("/gateway-app-releases", response_model=AppReleaseResponse, status_code=201)
async def handle_upload_app_release(
    request: Request,
    file: UploadFile = File(...),
    version: str = Form(...),
    mandatory: bool = Form(False),
    channel: str = Form("stable"),
    min_version: str | None = Form(None),
    changelog: str | None = Form(None),
    signature: str | None = Form(None),
    user: User = Depends(_admin),
    db: Session = Depends(get_db),
):
    """Upload a new gateway software release tarball."""
    release = upload_app_release(
        db,
        user,
        file=file,
        version=version,
        mandatory=mandatory,
        channel=channel,
        min_version=min_version,
        changelog=changelog,
        signature=signature,
        ip_address=request.client.host if request.client else None,
    )
    return AppReleaseResponse.model_validate(release)


@router.get("/gateway-app-releases", response_model=AppReleaseListResponse)
async def handle_list_app_releases(
    channel: str | None = None,
    is_active: bool | None = None,
    offset: int = 0,
    limit: int = 50,
    user: User = Depends(_admin),
    db: Session = Depends(get_db),
):
    """List all gateway app releases."""
    items, total = list_app_releases(db, channel=channel, is_active=is_active, offset=offset, limit=limit)
    return AppReleaseListResponse(
        items=[AppReleaseResponse.model_validate(r) for r in items],
        total=total,
    )


@router.patch("/gateway-app-releases/{release_id}/status", response_model=AppReleaseResponse)
async def handle_toggle_app_release(
    release_id: str,
    is_active: bool = True,
    request: Request = None,
    user: User = Depends(_admin),
    db: Session = Depends(get_db),
):
    """Activate or deactivate a release."""
    import uuid as _uuid

    release = toggle_app_release(
        db,
        user,
        _uuid.UUID(release_id),
        is_active,
        ip=request.client.host if request and request.client else None,
    )
    return AppReleaseResponse.model_validate(release)


@router.delete("/gateway-app-releases/{release_id}", status_code=204)
async def handle_delete_app_release(
    release_id: str,
    request: Request,
    user: User = Depends(_admin),
    db: Session = Depends(get_db),
):
    """Delete a gateway app release and its artifact."""
    import uuid as _uuid

    delete_app_release(
        db, user, _uuid.UUID(release_id), ip=request.client.host if request.client else None
    )


# ── Config Releases ──────────────────────────────────────────────────


@router.post("/gateway-config-releases", response_model=ConfigReleaseResponse, status_code=201)
async def handle_upload_config_release(
    request: Request,
    data: ConfigReleaseCreate,
    user: User = Depends(_admin),
    db: Session = Depends(get_db),
):
    """Upload a new gateway config release."""
    config = upload_config_release(
        db,
        user,
        version=data.version,
        config_payload=data.config_payload,
        schema_version=data.schema_version,
        compatible_app_min=data.compatible_app_min,
        compatible_app_max=data.compatible_app_max,
        ip_address=request.client.host if request.client else None,
    )
    return ConfigReleaseResponse.model_validate(config)


@router.get("/gateway-config-releases", response_model=ConfigReleaseListResponse)
async def handle_list_config_releases(
    is_active: bool | None = None,
    offset: int = 0,
    limit: int = 50,
    user: User = Depends(_admin),
    db: Session = Depends(get_db),
):
    items, total = list_config_releases(db, is_active=is_active, offset=offset, limit=limit)
    return ConfigReleaseListResponse(
        items=[ConfigReleaseResponse.model_validate(c) for c in items],
        total=total,
    )


@router.patch("/gateway-config-releases/{release_id}/status", response_model=ConfigReleaseResponse)
async def handle_toggle_config_release(
    release_id: str,
    is_active: bool = True,
    request: Request = None,
    user: User = Depends(_admin),
    db: Session = Depends(get_db),
):
    import uuid as _uuid

    config = toggle_config_release(
        db,
        user,
        _uuid.UUID(release_id),
        is_active,
        ip=request.client.host if request and request.client else None,
    )
    return ConfigReleaseResponse.model_validate(config)


# ── Desired State ────────────────────────────────────────────────────


@router.put("/gateway/{gateway_id}/desired-state")
async def handle_set_desired_state(
    gateway_id: str,
    data: DesiredStateUpdate,
    request: Request,
    user: User = Depends(_admin),
    db: Session = Depends(get_db),
):
    """Set or update the desired state for a specific gateway."""
    import uuid as _uuid

    ds = set_desired_state(
        db,
        user,
        _uuid.UUID(gateway_id),
        data.model_dump(exclude_none=True),
        ip=request.client.host if request.client else None,
    )
    return {"status": "ok", "gateway_id": gateway_id, "updated_at": str(ds.updated_at)}


# ── Remote Commands ──────────────────────────────────────────────────


@router.post("/gateway/{gateway_id}/command", response_model=CommandResponse, status_code=201)
async def handle_issue_command(
    gateway_id: str,
    data: CommandCreate,
    request: Request,
    user: User = Depends(_admin),
    db: Session = Depends(get_db),
):
    """Issue a remote command to a gateway (allowlist enforced)."""
    import uuid as _uuid

    cmd = issue_command(
        db,
        user,
        _uuid.UUID(gateway_id),
        data.command_type,
        data.payload,
        ip=request.client.host if request.client else None,
    )
    return CommandResponse.model_validate(cmd)


@router.get("/gateway/{gateway_id}/commands", response_model=CommandListResponse)
async def handle_list_commands(
    gateway_id: str,
    status: str | None = None,
    offset: int = 0,
    limit: int = 50,
    user: User = Depends(_admin),
    db: Session = Depends(get_db),
):
    """List commands for a gateway."""
    import uuid as _uuid

    q = db.query(GatewayCommand).filter(GatewayCommand.gateway_id == _uuid.UUID(gateway_id))
    if status:
        q = q.filter(GatewayCommand.status == status)
    total = q.count()
    items = q.order_by(GatewayCommand.created_at.desc()).offset(offset).limit(limit).all()
    return CommandListResponse(
        items=[CommandResponse.model_validate(c) for c in items],
        total=total,
    )


# ── Rollout ──────────────────────────────────────────────────────────


@router.post("/gateway-rollout", status_code=200)
async def handle_start_rollout(
    data: RolloutCreate,
    request: Request,
    user: User = Depends(_admin),
    db: Session = Depends(get_db),
):
    """Start a staged rollout of a gateway app release."""
    import uuid as _uuid

    z_id = data.zone_id
    count = initiate_rollout(
        db,
        user,
        data.release_version,
        data.target_ring,
        zone_id=z_id,
        ip=request.client.host if request.client else None,
    )
    return {"status": "ok", "gateways_updated": count}


# ── Rollback ─────────────────────────────────────────────────────────


@router.post("/gateway/{gateway_id}/rollback", status_code=200)
async def handle_rollback(
    gateway_id: str,
    data: RollbackRequest,
    request: Request,
    user: User = Depends(_admin),
    db: Session = Depends(get_db),
):
    """Initiate a rollback for a specific gateway."""
    import uuid as _uuid

    ds = initiate_rollback(
        db,
        user,
        _uuid.UUID(gateway_id),
        target_version=data.target_version,
        ip=request.client.host if request.client else None,
    )
    return {"status": "ok", "target_version": ds.desired_app_version}


# ── Maintenance ──────────────────────────────────────────────────────


@router.put("/gateway/{gateway_id}/maintenance", status_code=200)
async def handle_toggle_maintenance(
    gateway_id: str,
    enabled: bool = True,
    request: Request = None,
    user: User = Depends(_admin),
    db: Session = Depends(get_db),
):
    """Enable or disable maintenance mode for a gateway."""
    set_desired_state(
        db,
        user,
        __import__("uuid").UUID(gateway_id),
        {"maintenance_mode": enabled},
        ip=request.client.host if request and request.client else None,
    )
    return {"status": "ok", "maintenance_mode": enabled}


@router.put("/gateway/{gateway_id}/block", status_code=200)
async def handle_toggle_block(
    gateway_id: str,
    blocked: bool = True,
    request: Request = None,
    user: User = Depends(_admin),
    db: Session = Depends(get_db),
):
    """Block or unblock a gateway from receiving updates."""
    set_desired_state(
        db,
        user,
        __import__("uuid").UUID(gateway_id),
        {"blocked": blocked},
        ip=request.client.host if request and request.client else None,
    )
    return {"status": "ok", "blocked": blocked}


# ── Update Logs ──────────────────────────────────────────────────────


@router.get("/gateway-update-logs", response_model=UpdateLogListResponse)
async def handle_list_update_logs(
    gateway_id: str | None = None,
    update_type: str | None = None,
    offset: int = 0,
    limit: int = 50,
    user: User = Depends(_admin),
    db: Session = Depends(get_db),
):
    import uuid as _uuid

    gw_id = _uuid.UUID(gateway_id) if gateway_id else None
    items, total = list_update_logs(
        db, gateway_id=gw_id, update_type=update_type, offset=offset, limit=limit
    )
    return UpdateLogListResponse(
        items=[UpdateLogResponse(**i) for i in items],
        total=total,
    )


# ── Audit Logs ───────────────────────────────────────────────────────


@router.get("/gateway-audit-logs")
async def handle_gateway_audit_logs(
    action: str | None = None,
    offset: int = 0,
    limit: int = 50,
    user: User = Depends(_admin),
    db: Session = Depends(get_db),
):
    """Gateway-specific audit logs (filtered by gateway_ prefix)."""
    q = db.query(AuditLog).filter(AuditLog.action.like("gateway_%"))
    if action:
        q = q.filter(AuditLog.action == action)

    total = q.count()
    items = q.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit).all()
    return {
        "items": [
            {
                "id": a.id,
                "user_id": a.user_id,
                "action": a.action,
                "entity_type": a.entity_type,
                "entity_id": a.entity_id,
                "details": a.details,
                "ip_address": a.ip_address,
                "created_at": a.created_at,
            }
            for a in items
        ],
        "total": total,
    }
