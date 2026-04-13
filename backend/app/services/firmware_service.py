"""Firmware service layer: business logic for OTA management.

Keeps the router thin by centralising validation, file handling,
audit logging, and database queries here.
"""

import hashlib
import json
import os
import re
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.firmware import FirmwareRelease, FirmwareReport, RolloutPolicy
from app.models.master import Gateway, Sensor, Zone
from app.models.user import User

FIRMWARE_STORAGE_DIR = os.getenv("FIRMWARE_STORAGE_DIR", "/app/firmware_data")
MAX_FILE_SIZE = 16 * 1024 * 1024  # 16 MB
ALLOWED_EXTENSIONS = {".bin"}
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(-[\w.]+)?$")


# ── Audit helpers ────────────────────────────────────────────────────

def _audit(
    db: Session,
    user: User,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    details: str | None = None,
    ip_address: str | None = None,
) -> None:
    entry = AuditLog(
        user_id=user.id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details,
        ip_address=ip_address,
    )
    db.add(entry)
    # Commit happens in the calling function together with the main transaction


# ── Dashboard ────────────────────────────────────────────────────────

def get_dashboard_summary(db: Session) -> dict:
    now = datetime.now(UTC)
    h24_ago = now - timedelta(hours=24)

    active_releases = db.query(func.count(FirmwareRelease.id)).filter(
        FirmwareRelease.is_active.is_(True)
    ).scalar() or 0
    total_releases = db.query(func.count(FirmwareRelease.id)).scalar() or 0

    online_gateways = db.query(func.count(Gateway.id)).filter(
        Gateway.status == "online", Gateway.is_active.is_(True)
    ).scalar() or 0
    total_gateways = db.query(func.count(Gateway.id)).filter(
        Gateway.is_active.is_(True)
    ).scalar() or 0

    total_devices = db.query(func.count(Sensor.id)).scalar() or 0

    failed_24h = db.query(func.count(FirmwareReport.id)).filter(
        FirmwareReport.reported_at >= h24_ago,
        FirmwareReport.status.in_(["failed", "hash_mismatch", "rollback", "incompatible"]),
    ).scalar() or 0

    success_24h = db.query(func.count(FirmwareReport.id)).filter(
        FirmwareReport.reported_at >= h24_ago,
        FirmwareReport.status == "success",
    ).scalar() or 0

    active_rollouts = db.query(func.count(RolloutPolicy.id)).scalar() or 0

    return {
        "active_releases": active_releases,
        "total_releases": total_releases,
        "online_gateways": online_gateways,
        "total_gateways": total_gateways,
        "total_devices": total_devices,
        "failed_updates_24h": failed_24h,
        "successful_updates_24h": success_24h,
        "active_rollouts": active_rollouts,
    }


# ── Releases ─────────────────────────────────────────────────────────

def list_releases(
    db: Session,
    *,
    board_type: str | None = None,
    search: str | None = None,
    is_active: bool | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[FirmwareRelease], int]:
    q = db.query(FirmwareRelease)
    if board_type:
        q = q.filter(FirmwareRelease.board_type == board_type)
    if is_active is not None:
        q = q.filter(FirmwareRelease.is_active == is_active)
    if search:
        q = q.filter(FirmwareRelease.version.ilike(f"%{search}%"))
    total = q.count()
    items = q.order_by(FirmwareRelease.created_at.desc()).offset(offset).limit(limit).all()
    return items, total


def get_release(db: Session, release_id: uuid.UUID) -> FirmwareRelease:
    release = db.query(FirmwareRelease).filter(FirmwareRelease.id == release_id).first()
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")
    return release


def upload_release(
    db: Session,
    user: User,
    file: UploadFile,
    version: str,
    board_type: str,
    hardware_revision: str,
    mandatory: bool,
    min_version: str | None,
    changelog: str | None,
    ip_address: str | None = None,
) -> FirmwareRelease:
    # Validate version format
    if not SEMVER_RE.match(version):
        raise HTTPException(status_code=422, detail="Version must follow semver (e.g. 1.2.3)")

    # Validate file extension
    original_name = file.filename or ""
    ext = os.path.splitext(original_name)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=422, detail=f"Only {ALLOWED_EXTENSIONS} files allowed")

    # Read and validate size
    content = file.file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail=f"File exceeds {MAX_FILE_SIZE // (1024*1024)}MB limit")
    if len(content) == 0:
        raise HTTPException(status_code=422, detail="Empty file")

    # Compute SHA256
    sha256_hash = hashlib.sha256(content).hexdigest()

    # Check for duplicate version+board+revision
    existing = (
        db.query(FirmwareRelease)
        .filter(
            FirmwareRelease.version == version,
            FirmwareRelease.board_type == board_type,
            FirmwareRelease.hardware_revision == hardware_revision,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Release {version} for {board_type}/{hardware_revision} already exists",
        )

    # Safe filename: no user-controlled components except sanitised board/version
    safe_board = re.sub(r"[^a-zA-Z0-9_-]", "_", board_type)
    safe_version = re.sub(r"[^a-zA-Z0-9._-]", "_", version)
    safe_filename = f"{safe_board}_{safe_version}_{uuid.uuid4().hex[:8]}.bin"

    os.makedirs(FIRMWARE_STORAGE_DIR, exist_ok=True)
    file_loc = os.path.join(FIRMWARE_STORAGE_DIR, safe_filename)
    with open(file_loc, "wb") as f:
        f.write(content)

    release = FirmwareRelease(
        version=version,
        board_type=board_type,
        hardware_revision=hardware_revision,
        file_path=safe_filename,
        sha256=sha256_hash,
        is_active=False,  # Require explicit activation — safer default
        mandatory=mandatory,
        min_version=min_version,
        changelog=changelog,
    )
    db.add(release)
    _audit(db, user, "firmware.upload", "firmware_release", details=json.dumps({
        "version": version, "board_type": board_type, "sha256": sha256_hash,
    }), ip_address=ip_address)
    db.commit()
    db.refresh(release)
    return release


def toggle_release(
    db: Session, user: User, release_id: uuid.UUID, is_active: bool, ip: str | None = None
) -> FirmwareRelease:
    release = get_release(db, release_id)
    old_state = release.is_active
    release.is_active = is_active
    action = "firmware.activate" if is_active else "firmware.deactivate"
    _audit(db, user, action, "firmware_release", str(release.id), json.dumps({
        "version": release.version, "old_active": old_state, "new_active": is_active,
    }), ip_address=ip)
    db.commit()
    db.refresh(release)
    return release


def delete_release(
    db: Session, user: User, release_id: uuid.UUID, ip: str | None = None
) -> None:
    release = get_release(db, release_id)

    # Remove file from disk
    file_loc = os.path.join(FIRMWARE_STORAGE_DIR, release.file_path)
    if os.path.exists(file_loc):
        os.remove(file_loc)

    _audit(db, user, "firmware.delete", "firmware_release", str(release.id), json.dumps({
        "version": release.version, "board_type": release.board_type,
    }), ip_address=ip)

    db.delete(release)
    db.commit()


# ── Reports ──────────────────────────────────────────────────────────

def list_reports(
    db: Session,
    *,
    status: str | None = None,
    gateway_id: uuid.UUID | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[dict], int]:
    q = db.query(FirmwareReport)
    if status:
        q = q.filter(FirmwareReport.status == status)
    if gateway_id:
        q = q.filter(FirmwareReport.gateway_id == gateway_id)
    total = q.count()
    items = q.order_by(FirmwareReport.reported_at.desc()).offset(offset).limit(limit).all()

    # Enrich with release version and gateway name
    result = []
    for r in items:
        release = db.query(FirmwareRelease).filter(FirmwareRelease.id == r.release_id).first()
        gateway = db.query(Gateway).filter(Gateway.id == r.gateway_id).first()
        result.append({
            "id": r.id,
            "sensor_id": r.sensor_id,
            "gateway_id": r.gateway_id,
            "release_id": r.release_id,
            "status": r.status,
            "error_message": r.error_message,
            "reported_at": r.reported_at,
            "release_version": release.version if release else None,
            "gateway_name": gateway.name or gateway.hardware_id if gateway else None,
        })
    return result, total


# ── Rollout Policies ─────────────────────────────────────────────────

def list_policies(db: Session) -> list[dict]:
    items = db.query(RolloutPolicy).order_by(RolloutPolicy.created_at.desc()).all()
    result = []
    for p in items:
        release = db.query(FirmwareRelease).filter(FirmwareRelease.id == p.release_id).first()
        zone = db.query(Zone).filter(Zone.id == p.zone_id).first() if p.zone_id else None
        result.append({
            "id": p.id,
            "release_id": p.release_id,
            "zone_id": p.zone_id,
            "canary_percentage": p.canary_percentage,
            "created_at": p.created_at,
            "release_version": release.version if release else None,
            "zone_name": zone.name if zone else None,
        })
    return result


def create_policy(
    db: Session, user: User, release_id: uuid.UUID,
    zone_id: uuid.UUID | None, canary_percentage: str,
    ip: str | None = None,
) -> RolloutPolicy:
    # Verify release exists
    get_release(db, release_id)
    policy = RolloutPolicy(
        release_id=release_id,
        zone_id=zone_id,
        canary_percentage=canary_percentage,
    )
    db.add(policy)
    _audit(db, user, "rollout.create", "rollout_policy", details=json.dumps({
        "release_id": str(release_id), "zone_id": str(zone_id) if zone_id else None,
        "canary_percentage": canary_percentage,
    }), ip_address=ip)
    db.commit()
    db.refresh(policy)
    return policy


def delete_policy(
    db: Session, user: User, policy_id: uuid.UUID, ip: str | None = None
) -> None:
    policy = db.query(RolloutPolicy).filter(RolloutPolicy.id == policy_id).first()
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    _audit(db, user, "rollout.delete", "rollout_policy", str(policy.id), ip_address=ip)
    db.delete(policy)
    db.commit()


# ── Audit Logs ───────────────────────────────────────────────────────

def list_audit_logs(
    db: Session,
    *,
    action: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[dict], int]:
    from app.models.user import User as UserModel

    q = db.query(AuditLog)
    if action:
        q = q.filter(AuditLog.action == action)
    total = q.count()
    items = q.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit).all()

    result = []
    for a in items:
        user = db.query(UserModel).filter(UserModel.id == a.user_id).first() if a.user_id else None
        result.append({
            "id": a.id,
            "user_id": a.user_id,
            "user_email": user.email if user else None,
            "action": a.action,
            "entity_type": a.entity_type,
            "entity_id": a.entity_id,
            "details": a.details,
            "ip_address": a.ip_address,
            "created_at": a.created_at,
        })
    return result, total
