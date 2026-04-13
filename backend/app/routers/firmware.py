"""Firmware OTA router: gateway sync, device reports, and admin management.

Gateway endpoints authenticate via X-Api-Key header.
Admin endpoints require owner/admin role via JWT cookie.
"""

import uuid

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_role, verify_password
from app.database import get_db
from app.models.firmware import FirmwareRelease, FirmwareReport, RolloutPolicy
from app.models.master import Gateway, Sensor
from app.models.user import Role, User
from app.rate_limit import limiter
from app.schemas.firmware import (
    AuditLogListResponse,
    DashboardSummary,
    FirmwareReleaseListResponse,
    FirmwareReleaseResponse,
    FirmwareReportListResponse,
    FirmwareReportRequest,
    FirmwareSyncResponse,
    PaginationMeta,
    RolloutPolicyCreate,
    RolloutPolicyResponse,
)
from app.services import firmware_service as fw_svc

router = APIRouter(prefix="/firmware", tags=["firmware"])

# Reusable dependency for admin-only endpoints
_require_admin = require_role([Role.ADMIN, Role.OWNER])


def _auth_gateway(db: Session, api_key: str) -> Gateway:
    """Authenticate a gateway via its API key."""
    gateways = db.query(Gateway).filter(Gateway.is_active.is_(True)).all()
    for gw in gateways:
        if gw.api_key_hash and verify_password(api_key, gw.api_key_hash):
            return gw
    raise HTTPException(status_code=401, detail="Invalid API key")


# ─────────────────────────────────────────────────────────────────────
# Gateway Endpoints (machine-to-machine, X-Api-Key auth)
# ─────────────────────────────────────────────────────────────────────

@router.get("/sync", response_model=list[FirmwareSyncResponse])
async def sync_firmware(
    request: Request,
    db: Session = Depends(get_db),
    x_api_key: str | None = Header(None),
):
    """Gateway polls for applicable firmware releases."""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing API Key")
    gw = _auth_gateway(db, x_api_key)

    active_releases = db.query(FirmwareRelease).filter(FirmwareRelease.is_active.is_(True)).all()

    applicable = []
    for release in active_releases:
        policies = db.query(RolloutPolicy).filter(RolloutPolicy.release_id == release.id).all()
        if policies:
            zone_restricted = any(p.zone_id for p in policies)
            allowed_zones = [p.zone_id for p in policies if p.zone_id]
            if zone_restricted and gw.zone_id not in allowed_zones:
                continue

        applicable.append(
            FirmwareSyncResponse(
                id=release.id,
                version=release.version,
                board_type=release.board_type,
                hardware_revision=release.hardware_revision,
                firmware_url=f"/firmware/{release.file_path}",
                sha256=release.sha256,
                mandatory=release.mandatory,
                min_version=release.min_version,
                changelog=release.changelog,
            )
        )
    return applicable


@router.post("/report", status_code=201)
async def report_firmware_status(
    data: FirmwareReportRequest,
    db: Session = Depends(get_db),
    x_api_key: str | None = Header(None),
):
    """Gateway reports update success or failure for a device."""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing API Key")
    gw = _auth_gateway(db, x_api_key)

    sensor_id = None
    if data.sensor_mac:
        sensor = (
            db.query(Sensor)
            .filter(Sensor.mac_address == data.sensor_mac, Sensor.gateway_id == gw.id)
            .first()
        )
        if sensor:
            sensor_id = sensor.id

    report = FirmwareReport(
        gateway_id=gw.id,
        sensor_id=sensor_id,
        release_id=data.release_id,
        status=data.status,
        error_message=data.error_message,
    )
    db.add(report)
    db.commit()
    return {"status": "recorded"}


# ─────────────────────────────────────────────────────────────────────
# Admin Endpoints (JWT auth, role-gated)
# ─────────────────────────────────────────────────────────────────────

@router.get("/dashboard", response_model=DashboardSummary)
async def dashboard(
    current_user: User = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """Summary stats for the admin dashboard."""
    return fw_svc.get_dashboard_summary(db)


@router.get("/releases", response_model=FirmwareReleaseListResponse)
async def list_releases(
    board_type: str | None = Query(None),
    search: str | None = Query(None),
    is_active: bool | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """Paginated list of firmware releases with optional filters."""
    items, total = fw_svc.list_releases(
        db, board_type=board_type, search=search, is_active=is_active,
        offset=offset, limit=limit,
    )
    return FirmwareReleaseListResponse(
        items=items,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


@router.get("/releases/{release_id}", response_model=FirmwareReleaseResponse)
async def get_release(
    release_id: uuid.UUID,
    current_user: User = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """Single release detail."""
    return fw_svc.get_release(db, release_id)


@router.post("/upload", response_model=FirmwareReleaseResponse, status_code=201)
@limiter.limit("10/minute")
async def upload_firmware(
    request: Request,
    version: str = Form(...),
    board_type: str = Form(...),
    hardware_revision: str = Form(...),
    mandatory: bool = Form(False),
    min_version: str | None = Form(None),
    changelog: str | None = Form(None),
    file: UploadFile = File(...),
    current_user: User = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """Upload a new firmware binary. Rate-limited to 10/min."""
    return fw_svc.upload_release(
        db, current_user, file, version, board_type, hardware_revision,
        mandatory, min_version, changelog,
        ip_address=request.client.host if request.client else None,
    )


@router.patch("/releases/{release_id}/status", response_model=FirmwareReleaseResponse)
async def toggle_release_status(
    request: Request,
    release_id: uuid.UUID,
    is_active: bool = Query(...),
    current_user: User = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """Activate or deactivate a firmware release."""
    return fw_svc.toggle_release(
        db, current_user, release_id, is_active,
        ip=request.client.host if request.client else None,
    )


@router.delete("/releases/{release_id}", status_code=204)
async def delete_release(
    request: Request,
    release_id: uuid.UUID,
    current_user: User = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """Permanently delete a release and its file."""
    fw_svc.delete_release(
        db, current_user, release_id,
        ip=request.client.host if request.client else None,
    )


# ── Reports ──────────────────────────────────────────────────────────

@router.get("/reports", response_model=FirmwareReportListResponse)
async def list_reports(
    status: str | None = Query(None),
    gateway_id: uuid.UUID | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """Paginated list of device update reports."""
    items, total = fw_svc.list_reports(
        db, status=status, gateway_id=gateway_id, offset=offset, limit=limit,
    )
    return FirmwareReportListResponse(
        items=items,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ── Rollout Policies ─────────────────────────────────────────────────

@router.get("/policies")
async def list_policies(
    current_user: User = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """List all rollout policies."""
    return fw_svc.list_policies(db)


@router.post("/policies", response_model=RolloutPolicyResponse, status_code=201)
async def create_rollout_policy(
    request: Request,
    policy: RolloutPolicyCreate,
    current_user: User = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """Create a rollout policy for a release."""
    p = fw_svc.create_policy(
        db, current_user, policy.release_id, policy.zone_id,
        policy.canary_percentage,
        ip=request.client.host if request.client else None,
    )
    # Re-fetch enriched
    policies = fw_svc.list_policies(db)
    match = next((x for x in policies if x["id"] == p.id), None)
    return match or p


@router.delete("/policies/{policy_id}", status_code=204)
async def delete_rollout_policy(
    request: Request,
    policy_id: uuid.UUID,
    current_user: User = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """Delete a rollout policy."""
    fw_svc.delete_policy(
        db, current_user, policy_id,
        ip=request.client.host if request.client else None,
    )


# ── Audit Logs ───────────────────────────────────────────────────────

@router.get("/audit-logs", response_model=AuditLogListResponse)
async def list_audit_logs(
    action: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """Admin action audit trail."""
    items, total = fw_svc.list_audit_logs(
        db, action=action, offset=offset, limit=limit,
    )
    return AuditLogListResponse(
        items=items,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )
