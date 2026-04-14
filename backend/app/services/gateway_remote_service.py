"""Business logic for gateway remote management.

Handles desired-state resolution, release management, config validation,
command allowlisting, fleet overview, and audit logging.
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
from app.models.gateway_remote import (
    GatewayAppRelease,
    GatewayCommand,
    GatewayConfigRelease,
    GatewayDesiredState,
    GatewayStateReport,
    GatewayUpdateLog,
)
from app.models.master import Gateway, Zone
from app.models.user import User
from app.schemas.gateway_remote import (
    ALLOWED_COMMAND_TYPES,
    DesiredStateResponse,
    GatewayFleetItem,
    PendingCommandResponse,
)

GATEWAY_RELEASE_DIR = os.getenv("GATEWAY_RELEASE_DIR", "/app/gateway_releases")
MAX_RELEASE_SIZE = 100 * 1024 * 1024  # 100 MB
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(-[\w.]+)?$")
COMMAND_TTL_HOURS = 1


# ── Audit ────────────────────────────────────────────────────────────


def _audit(
    db: Session,
    user: User | None,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    details: str | None = None,
    ip_address: str | None = None,
) -> None:
    entry = AuditLog(
        user_id=user.id if user else None,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details,
        ip_address=ip_address,
    )
    db.add(entry)


# ── Desired State ────────────────────────────────────────────────────


def get_desired_state(
    db: Session,
    gateway: Gateway,
    current_app_version: str | None = None,
    current_config_version: str | None = None,
) -> DesiredStateResponse:
    """Build the full desired-state response for an agent poll."""

    ds = (
        db.query(GatewayDesiredState)
        .filter(GatewayDesiredState.gateway_id == gateway.id)
        .first()
    )

    if not ds:
        # No desired state set yet — return defaults
        return DesiredStateResponse()

    if ds.blocked:
        return DesiredStateResponse(blocked=True, maintenance_mode=ds.maintenance_mode)

    # Check if app update is available
    app_update = False
    app_artifact_url = None
    app_sha256 = None
    app_signature = None
    app_file_size = None
    app_mandatory = False
    release_notes = None

    if ds.desired_app_version and ds.desired_app_version != current_app_version:
        release = (
            db.query(GatewayAppRelease)
            .filter(
                GatewayAppRelease.version == ds.desired_app_version,
                GatewayAppRelease.is_active.is_(True),
            )
            .first()
        )
        if release:
            # Downgrade protection
            if (
                current_app_version
                and not ds.force_downgrade
                and _is_downgrade(current_app_version, ds.desired_app_version)
            ):
                app_update = False
            else:
                app_update = True
                app_artifact_url = f"/api/v1/gateway/app-release/{release.version}/download"
                app_sha256 = release.sha256
                app_signature = release.signature
                app_file_size = release.file_size_bytes
                app_mandatory = release.mandatory
                release_notes = release.changelog

    # Check if config update is available
    config_update = False
    config_artifact_url = None
    config_sha256 = None

    if ds.desired_config_version and ds.desired_config_version != current_config_version:
        config = (
            db.query(GatewayConfigRelease)
            .filter(
                GatewayConfigRelease.version == ds.desired_config_version,
                GatewayConfigRelease.is_active.is_(True),
            )
            .first()
        )
        if config:
            config_update = True
            config_artifact_url = f"/api/v1/gateway/config-release/{config.version}/download"
            config_sha256 = config.sha256

    # Collect pending commands (not expired, not yet executed)
    now = datetime.now(UTC)
    pending_cmds = (
        db.query(GatewayCommand)
        .filter(
            GatewayCommand.gateway_id == gateway.id,
            GatewayCommand.status == "pending",
            GatewayCommand.expires_at > now,
        )
        .order_by(GatewayCommand.created_at.asc())
        .all()
    )

    # Mark as delivered
    for cmd in pending_cmds:
        cmd.status = "delivered"
        cmd.delivered_at = now
    if pending_cmds:
        db.commit()

    return DesiredStateResponse(
        desired_app_version=ds.desired_app_version,
        desired_config_version=ds.desired_config_version,
        app_update_available=app_update,
        config_update_available=config_update,
        app_artifact_url=app_artifact_url,
        config_artifact_url=config_artifact_url,
        app_sha256=app_sha256,
        config_sha256=config_sha256,
        app_signature=app_signature,
        app_file_size_bytes=app_file_size,
        app_mandatory=app_mandatory,
        maintenance_mode=ds.maintenance_mode,
        reboot_allowed=ds.reboot_allowed,
        blocked=ds.blocked,
        rollout_ring=ds.rollout_ring,
        force_downgrade=ds.force_downgrade,
        release_notes=release_notes,
        update_window_start=ds.update_window_start,
        update_window_end=ds.update_window_end,
        update_timezone=ds.update_timezone,
        allow_download_outside_window=ds.allow_download_outside_window,
        allow_apply_outside_window=ds.allow_apply_outside_window,
        allow_reboot_outside_window=ds.allow_reboot_outside_window,
        pending_commands=[
            PendingCommandResponse(
                id=c.id,
                command_type=c.command_type,
                payload=c.payload,
                created_at=c.created_at,
                expires_at=c.expires_at,
            )
            for c in pending_cmds
        ],
    )


def _is_downgrade(current: str, desired: str) -> bool:
    """Simple semver comparison: True if desired < current."""
    try:
        from packaging import version as semver

        return semver.parse(desired) < semver.parse(current)
    except Exception:
        return False


# ── State Reports ────────────────────────────────────────────────────


def process_state_report(db: Session, gateway: Gateway, data: dict) -> None:
    """Record agent state report and update gateway metadata."""

    report = GatewayStateReport(
        gateway_id=gateway.id,
        app_version=data.get("app_version"),
        config_version=data.get("config_version"),
        agent_version=data.get("agent_version"),
        status=data.get("status", "idle"),
        health_status=data.get("health_status"),
        disk_free_mb=data.get("disk_free_mb"),
        cpu_temp_c=data.get("cpu_temp_c"),
        ram_usage_pct=data.get("ram_usage_pct"),
        uptime_seconds=data.get("uptime_seconds"),
        last_error=data.get("last_error"),
        update_download_status=data.get("update_download_status"),
        update_apply_status=data.get("update_apply_status"),
        signature_status=data.get("signature_status"),
    )
    db.add(report)

    # Update gateway columns
    gateway.last_seen = datetime.now(UTC)
    gateway.status = "online"
    if data.get("app_version"):
        gateway.app_version = data["app_version"]
    if data.get("config_version"):
        gateway.config_version = data["config_version"]
    if data.get("agent_version"):
        gateway.agent_version = data["agent_version"]
    if data.get("disk_free_mb") is not None:
        gateway.disk_free_mb = data["disk_free_mb"]
    if data.get("update_download_status"):
        gateway.update_download_status = data["update_download_status"]
    if data.get("update_apply_status"):
        gateway.update_apply_status = data["update_apply_status"]
    if data.get("signature_status"):
        gateway.signature_status = data["signature_status"]

    db.commit()


# ── Command Results ──────────────────────────────────────────────────


def process_command_result(
    db: Session, gateway: Gateway, command_id: uuid.UUID, result: str, message: str | None
) -> None:
    """Record command execution result from agent."""

    cmd = (
        db.query(GatewayCommand)
        .filter(
            GatewayCommand.id == command_id,
            GatewayCommand.gateway_id == gateway.id,
        )
        .first()
    )
    if not cmd:
        raise HTTPException(status_code=404, detail="Command not found")

    cmd.status = result  # executed / failed / rejected
    cmd.executed_at = datetime.now(UTC)
    cmd.result_message = message
    db.commit()


# ── App Release Management ──────────────────────────────────────────


def upload_app_release(
    db: Session,
    user: User,
    file: UploadFile,
    version: str,
    mandatory: bool,
    channel: str,
    min_version: str | None,
    changelog: str | None,
    signature: str | None,
    ip_address: str | None = None,
) -> GatewayAppRelease:
    """Upload a new gateway software release tarball."""

    if not SEMVER_RE.match(version):
        raise HTTPException(status_code=422, detail="Version must follow semver (e.g. 1.2.0)")

    existing = db.query(GatewayAppRelease).filter(GatewayAppRelease.version == version).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Release {version} already exists")

    content = file.file.read()
    if len(content) > MAX_RELEASE_SIZE:
        raise HTTPException(status_code=413, detail="File exceeds 100MB limit")
    if len(content) == 0:
        raise HTTPException(status_code=422, detail="Empty file")

    sha256_hash = hashlib.sha256(content).hexdigest()

    # Sanitised filename
    safe_version = re.sub(r"[^a-zA-Z0-9._-]", "_", version)
    safe_filename = f"greenmind-gateway-{safe_version}.tar.gz"

    os.makedirs(GATEWAY_RELEASE_DIR, exist_ok=True)
    file_path = os.path.join(GATEWAY_RELEASE_DIR, safe_filename)
    with open(file_path, "wb") as f:
        f.write(content)

    release = GatewayAppRelease(
        version=version,
        artifact_path=safe_filename,
        sha256=sha256_hash,
        signature=signature,
        mandatory=mandatory,
        is_active=False,  # Require explicit activation
        channel=channel,
        min_version=min_version,
        file_size_bytes=len(content),
        changelog=changelog,
        created_by=user.id,
    )
    db.add(release)
    _audit(
        db,
        user,
        "gateway_release.upload",
        "gateway_app_release",
        details=json.dumps({"version": version, "sha256": sha256_hash, "channel": channel}),
        ip_address=ip_address,
    )
    db.commit()
    db.refresh(release)
    return release


def list_app_releases(
    db: Session,
    *,
    channel: str | None = None,
    is_active: bool | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[GatewayAppRelease], int]:
    q = db.query(GatewayAppRelease)
    if channel:
        q = q.filter(GatewayAppRelease.channel == channel)
    if is_active is not None:
        q = q.filter(GatewayAppRelease.is_active == is_active)
    total = q.count()
    items = q.order_by(GatewayAppRelease.created_at.desc()).offset(offset).limit(limit).all()
    return items, total


def toggle_app_release(
    db: Session, user: User, release_id: uuid.UUID, is_active: bool, ip: str | None = None
) -> GatewayAppRelease:
    release = db.query(GatewayAppRelease).filter(GatewayAppRelease.id == release_id).first()
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")

    old = release.is_active
    release.is_active = is_active
    action = "gateway_release.activate" if is_active else "gateway_release.deactivate"
    _audit(
        db,
        user,
        action,
        "gateway_app_release",
        str(release.id),
        json.dumps({"version": release.version, "old_active": old, "new_active": is_active}),
        ip_address=ip,
    )
    db.commit()
    db.refresh(release)
    return release


def delete_app_release(
    db: Session, user: User, release_id: uuid.UUID, ip: str | None = None
) -> None:
    release = db.query(GatewayAppRelease).filter(GatewayAppRelease.id == release_id).first()
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")

    file_path = os.path.join(GATEWAY_RELEASE_DIR, release.artifact_path)
    if os.path.exists(file_path):
        os.remove(file_path)

    _audit(
        db,
        user,
        "gateway_release.delete",
        "gateway_app_release",
        str(release.id),
        json.dumps({"version": release.version}),
        ip_address=ip,
    )
    db.delete(release)
    db.commit()


# ── Config Release Management ────────────────────────────────────────


def upload_config_release(
    db: Session,
    user: User,
    version: str,
    config_payload: dict,
    schema_version: str,
    compatible_app_min: str | None,
    compatible_app_max: str | None,
    ip_address: str | None = None,
) -> GatewayConfigRelease:
    """Upload a new gateway config release."""

    existing = db.query(GatewayConfigRelease).filter(GatewayConfigRelease.version == version).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Config version {version} already exists")

    # Validate config is valid JSON (it already is dict from pydantic, but ensure serialisable)
    try:
        serialised = json.dumps(config_payload, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Config not serialisable: {exc}") from exc

    sha256_hash = hashlib.sha256(serialised.encode()).hexdigest()

    config = GatewayConfigRelease(
        version=version,
        config_payload=config_payload,
        schema_version=schema_version,
        compatible_app_min=compatible_app_min,
        compatible_app_max=compatible_app_max,
        sha256=sha256_hash,
        is_active=False,
        created_by=user.id,
    )
    db.add(config)
    _audit(
        db,
        user,
        "gateway_config.upload",
        "gateway_config_release",
        details=json.dumps({"version": version, "sha256": sha256_hash}),
        ip_address=ip_address,
    )
    db.commit()
    db.refresh(config)
    return config


def list_config_releases(
    db: Session, *, is_active: bool | None = None, offset: int = 0, limit: int = 50
) -> tuple[list[GatewayConfigRelease], int]:
    q = db.query(GatewayConfigRelease)
    if is_active is not None:
        q = q.filter(GatewayConfigRelease.is_active == is_active)
    total = q.count()
    items = q.order_by(GatewayConfigRelease.created_at.desc()).offset(offset).limit(limit).all()
    return items, total


def toggle_config_release(
    db: Session, user: User, release_id: uuid.UUID, is_active: bool, ip: str | None = None
) -> GatewayConfigRelease:
    config = db.query(GatewayConfigRelease).filter(GatewayConfigRelease.id == release_id).first()
    if not config:
        raise HTTPException(status_code=404, detail="Config release not found")

    config.is_active = is_active
    _audit(
        db,
        user,
        "gateway_config.activate" if is_active else "gateway_config.deactivate",
        "gateway_config_release",
        str(config.id),
        ip_address=ip,
    )
    db.commit()
    db.refresh(config)
    return config


# ── Desired State Admin ──────────────────────────────────────────────


def set_desired_state(
    db: Session, user: User, gateway_id: uuid.UUID, updates: dict, ip: str | None = None
) -> GatewayDesiredState:
    """Create or update the desired state for a gateway."""

    gateway = db.query(Gateway).filter(Gateway.id == gateway_id).first()
    if not gateway:
        raise HTTPException(status_code=404, detail="Gateway not found")

    ds = (
        db.query(GatewayDesiredState)
        .filter(GatewayDesiredState.gateway_id == gateway_id)
        .first()
    )

    if not ds:
        ds = GatewayDesiredState(gateway_id=gateway_id)
        db.add(ds)

    # Apply only provided fields (partial update)
    for field, value in updates.items():
        if value is not None and hasattr(ds, field):
            setattr(ds, field, value)

    ds.updated_by = user.id
    ds.updated_at = datetime.now(UTC)

    _audit(
        db,
        user,
        "gateway_desired_state.update",
        "gateway_desired_state",
        str(gateway_id),
        json.dumps({k: str(v) for k, v in updates.items() if v is not None}),
        ip_address=ip,
    )
    db.commit()
    db.refresh(ds)
    return ds


# ── Commands ─────────────────────────────────────────────────────────


def issue_command(
    db: Session,
    user: User,
    gateway_id: uuid.UUID,
    command_type: str,
    payload: dict | None = None,
    ip: str | None = None,
) -> GatewayCommand:
    """Issue a remote command to a gateway (allowlist enforced)."""

    if command_type not in ALLOWED_COMMAND_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Command '{command_type}' not in allowlist: {sorted(ALLOWED_COMMAND_TYPES)}",
        )

    gateway = db.query(Gateway).filter(Gateway.id == gateway_id).first()
    if not gateway:
        raise HTTPException(status_code=404, detail="Gateway not found")

    now = datetime.now(UTC)
    cmd = GatewayCommand(
        gateway_id=gateway_id,
        command_type=command_type,
        payload=payload,
        status="pending",
        created_by=user.id,
        expires_at=now + timedelta(hours=COMMAND_TTL_HOURS),
    )
    db.add(cmd)

    _audit(
        db,
        user,
        "gateway_command.issue",
        "gateway_command",
        details=json.dumps({"gateway_id": str(gateway_id), "command_type": command_type}),
        ip_address=ip,
    )
    db.commit()
    db.refresh(cmd)
    return cmd


def expire_stale_commands(db: Session) -> int:
    """Mark expired pending/delivered commands. Returns count of expired."""
    now = datetime.now(UTC)
    stale = (
        db.query(GatewayCommand)
        .filter(
            GatewayCommand.status.in_(["pending", "delivered"]),
            GatewayCommand.expires_at <= now,
        )
        .all()
    )
    for cmd in stale:
        cmd.status = "expired"
    if stale:
        db.commit()
    return len(stale)


# ── Fleet Overview ───────────────────────────────────────────────────


def get_fleet_overview(
    db: Session,
    *,
    zone_id: uuid.UUID | None = None,
    status_filter: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[GatewayFleetItem], int]:
    """Aggregated fleet view for admin dashboard."""

    now = datetime.now(UTC)
    liveness = timedelta(minutes=5)

    q = db.query(Gateway).filter(Gateway.is_active.is_(True))
    if zone_id:
        q = q.filter(Gateway.zone_id == zone_id)

    total = q.count()
    gateways = q.order_by(Gateway.created_at.desc()).offset(offset).limit(limit).all()

    items = []
    for gw in gateways:
        is_online = bool(gw.last_seen and (now - gw.last_seen) < liveness)
        gw_status = "online" if is_online else "offline"

        if status_filter and gw_status != status_filter:
            continue

        zone = db.query(Zone).filter(Zone.id == gw.zone_id).first()

        ds = (
            db.query(GatewayDesiredState)
            .filter(GatewayDesiredState.gateway_id == gw.id)
            .first()
        )

        # Disk status classification
        disk_status = None
        disk_free = getattr(gw, "disk_free_mb", None)
        if disk_free is not None:
            if disk_free < 100:
                disk_status = "critical"
            elif disk_free < 500:
                disk_status = "low"
            else:
                disk_status = "ok"

        # Format update window
        update_window = None
        if ds and ds.update_window_start and ds.update_window_end:
            update_window = f"{ds.update_window_start}–{ds.update_window_end} {ds.update_timezone}"

        items.append(
            GatewayFleetItem(
                id=gw.id,
                hardware_id=gw.hardware_id,
                name=gw.name,
                zone_name=zone.name if zone else None,
                status=gw_status,
                app_version=getattr(gw, "app_version", None),
                config_version=getattr(gw, "config_version", None),
                agent_version=getattr(gw, "agent_version", None),
                rollout_ring=getattr(gw, "rollout_ring", "stable"),
                maintenance_mode=getattr(gw, "maintenance_mode", False),
                blocked=getattr(gw, "blocked", False),
                disk_free_mb=disk_free,
                disk_status=disk_status,
                update_download_status=getattr(gw, "update_download_status", None),
                update_apply_status=getattr(gw, "update_apply_status", None),
                signature_status=getattr(gw, "signature_status", None),
                update_window=update_window,
                last_seen=gw.last_seen,
                desired_app_version=ds.desired_app_version if ds else None,
                desired_config_version=ds.desired_config_version if ds else None,
            )
        )

    return items, total


# ── Update Logs ──────────────────────────────────────────────────────


def list_update_logs(
    db: Session,
    *,
    gateway_id: uuid.UUID | None = None,
    update_type: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[dict], int]:
    q = db.query(GatewayUpdateLog)
    if gateway_id:
        q = q.filter(GatewayUpdateLog.gateway_id == gateway_id)
    if update_type:
        q = q.filter(GatewayUpdateLog.update_type == update_type)

    total = q.count()
    items = q.order_by(GatewayUpdateLog.started_at.desc()).offset(offset).limit(limit).all()

    result = []
    for log in items:
        gw = db.query(Gateway).filter(Gateway.id == log.gateway_id).first()
        result.append(
            {
                "id": log.id,
                "gateway_id": log.gateway_id,
                "gateway_name": gw.name or gw.hardware_id if gw else None,
                "update_type": log.update_type,
                "from_version": log.from_version,
                "to_version": log.to_version,
                "status": log.status,
                "error_message": log.error_message,
                "started_at": log.started_at,
                "completed_at": log.completed_at,
            }
        )
    return result, total


# ── Rollout ──────────────────────────────────────────────────────────


def initiate_rollout(
    db: Session,
    user: User,
    release_version: str,
    target_ring: str = "canary",
    zone_id: uuid.UUID | None = None,
    ip: str | None = None,
) -> int:
    """Set desired_app_version for gateways in the target ring (and optional zone).

    Returns the number of gateways updated.
    """
    release = (
        db.query(GatewayAppRelease)
        .filter(GatewayAppRelease.version == release_version, GatewayAppRelease.is_active.is_(True))
        .first()
    )
    if not release:
        raise HTTPException(status_code=404, detail="Active release not found")

    q = db.query(Gateway).filter(Gateway.is_active.is_(True))
    if zone_id:
        q = q.filter(Gateway.zone_id == zone_id)

    gateways = q.all()
    count = 0

    for gw in gateways:
        ds = db.query(GatewayDesiredState).filter(GatewayDesiredState.gateway_id == gw.id).first()
        if not ds:
            ds = GatewayDesiredState(gateway_id=gw.id)
            db.add(ds)

        # Only update gateways in the target ring
        if ds.rollout_ring == target_ring or target_ring == "all":
            ds.desired_app_version = release_version
            ds.updated_by = user.id
            ds.updated_at = datetime.now(UTC)
            count += 1

    _audit(
        db,
        user,
        "gateway_rollout.start",
        "gateway_app_release",
        str(release.id),
        json.dumps({
            "version": release_version,
            "ring": target_ring,
            "zone_id": str(zone_id) if zone_id else None,
            "gateways_affected": count,
        }),
        ip_address=ip,
    )
    db.commit()
    return count


def initiate_rollback(
    db: Session,
    user: User,
    gateway_id: uuid.UUID,
    target_version: str | None = None,
    ip: str | None = None,
) -> GatewayDesiredState:
    """Roll back a gateway to a previous (or specified) version."""

    gateway = db.query(Gateway).filter(Gateway.id == gateway_id).first()
    if not gateway:
        raise HTTPException(status_code=404, detail="Gateway not found")

    if not target_version:
        # Auto-detect: find the previous successful update
        last_success = (
            db.query(GatewayUpdateLog)
            .filter(
                GatewayUpdateLog.gateway_id == gateway_id,
                GatewayUpdateLog.update_type == "app",
                GatewayUpdateLog.status == "apply_success",
            )
            .order_by(GatewayUpdateLog.completed_at.desc())
            .first()
        )
        if last_success:
            target_version = last_success.from_version
        if not target_version:
            raise HTTPException(status_code=404, detail="No previous version found for rollback")

    ds = db.query(GatewayDesiredState).filter(GatewayDesiredState.gateway_id == gateway_id).first()
    if not ds:
        ds = GatewayDesiredState(gateway_id=gateway_id)
        db.add(ds)

    ds.desired_app_version = target_version
    ds.force_downgrade = True
    ds.updated_by = user.id
    ds.updated_at = datetime.now(UTC)

    _audit(
        db,
        user,
        "gateway_rollback.initiate",
        "gateway_desired_state",
        str(gateway_id),
        json.dumps({"target_version": target_version}),
        ip_address=ip,
    )
    db.commit()
    db.refresh(ds)
    return ds
