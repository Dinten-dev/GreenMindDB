"""Firmware OTA router: gateway sync, device reports, and admin management.

Gateway endpoints authenticate via X-Api-Key header.
Admin endpoints require owner/admin role via JWT cookie.
"""

import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.auth import require_role
from app.database import get_db
from app.file_security import resolve_contained_path, verify_file_sha256
from app.gateway_auth import get_current_gateway
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
_require_admin = require_role([Role.ADMIN])


def _release_applies_to_gateway(
    db: Session,
    release: FirmwareRelease,
    gateway: Gateway,
) -> bool:
    policies = db.query(RolloutPolicy).filter(RolloutPolicy.release_id == release.id).all()
    allowed_zones = {policy.zone_id for policy in policies if policy.zone_id}
    return not allowed_zones or gateway.zone_id in allowed_zones


# ─────────────────────────────────────────────────────────────────────
# Gateway Endpoints (machine-to-machine, X-Api-Key auth)
# ─────────────────────────────────────────────────────────────────────


@router.get("/sync", response_model=list[FirmwareSyncResponse])
async def sync_firmware(
    gw: Gateway = Depends(get_current_gateway),
    db: Session = Depends(get_db),
):
    """Gateway polls for applicable firmware releases."""
    active_releases = db.query(FirmwareRelease).filter(FirmwareRelease.is_active.is_(True)).all()

    applicable = []
    for release in active_releases:
        if not _release_applies_to_gateway(db, release, gw):
            continue

        applicable.append(
            FirmwareSyncResponse(
                id=release.id,
                version=release.version,
                board_type=release.board_type,
                hardware_revision=release.hardware_revision,
                firmware_url=f"/api/v1/firmware/download/{release.id}",
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
    gw: Gateway = Depends(get_current_gateway),
    db: Session = Depends(get_db),
):
    """Gateway reports update success or failure for a device."""
    release = (
        db.query(FirmwareRelease)
        .filter(FirmwareRelease.id == data.release_id, FirmwareRelease.is_active.is_(True))
        .first()
    )
    if not release or not _release_applies_to_gateway(db, release, gw):
        raise HTTPException(status_code=404, detail="Firmware release not found")

    sensor_id = None
    if data.sensor_mac:
        sensor = (
            db.query(Sensor)
            .filter(Sensor.mac_address == data.sensor_mac, Sensor.gateway_id == gw.id)
            .first()
        )
        if not sensor:
            raise HTTPException(status_code=403, detail="Sensor is not assigned to this gateway")
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


@router.get("/download/{release_id}")
async def download_firmware(
    release_id: uuid.UUID,
    gateway: Gateway = Depends(get_current_gateway),
    db: Session = Depends(get_db),
):
    """Download an active firmware artifact authorized for this gateway."""
    release = (
        db.query(FirmwareRelease)
        .filter(FirmwareRelease.id == release_id, FirmwareRelease.is_active.is_(True))
        .first()
    )
    if not release or not _release_applies_to_gateway(db, release, gateway):
        raise HTTPException(status_code=404, detail="Firmware release not found")
    try:
        file_path = resolve_contained_path(fw_svc.FIRMWARE_STORAGE_DIR, release.file_path)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Firmware artifact path is invalid") from exc
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Firmware artifact is missing")
    try:
        verify_file_sha256(file_path, release.sha256)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Firmware artifact checksum mismatch") from exc
    return FileResponse(
        file_path,
        media_type="application/octet-stream",
        filename=file_path.name,
        headers={"X-Checksum-SHA256": release.sha256},
    )


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
        db,
        board_type=board_type,
        search=search,
        is_active=is_active,
        offset=offset,
        limit=limit,
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
        db,
        current_user,
        file,
        version,
        board_type,
        hardware_revision,
        mandatory,
        min_version,
        changelog,
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
        db,
        current_user,
        release_id,
        is_active,
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
        db,
        current_user,
        release_id,
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
        db,
        status=status,
        gateway_id=gateway_id,
        offset=offset,
        limit=limit,
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
        db,
        current_user,
        policy.release_id,
        policy.zone_id,
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
        db,
        current_user,
        policy_id,
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
        db,
        action=action,
        offset=offset,
        limit=limit,
    )
    return AuditLogListResponse(
        items=items,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )
