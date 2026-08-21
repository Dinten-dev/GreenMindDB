"""Pydantic schemas for gateway remote management endpoints."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# ── Desired State (agent-facing) ─────────────────────────────────────


class DesiredStateRequest(BaseModel):
    """Query params sent by the agent when polling desired state."""

    gateway_id: uuid.UUID
    current_app_version: str | None = Field(None, max_length=50)
    current_config_version: str | None = Field(None, max_length=50)
    current_agent_version: str | None = Field(None, max_length=50)


class PendingCommandResponse(BaseModel):
    id: uuid.UUID
    command_type: str
    payload: dict | None = None
    created_at: datetime
    expires_at: datetime


class DesiredStateResponse(BaseModel):
    """Full desired state payload returned to the agent."""

    desired_app_version: str | None = None
    desired_config_version: str | None = None
    app_update_available: bool = False
    config_update_available: bool = False
    app_artifact_url: str | None = None
    config_artifact_url: str | None = None
    app_sha256: str | None = None
    config_sha256: str | None = None
    app_signature: str | None = None
    app_file_size_bytes: int | None = None
    app_mandatory: bool = False
    maintenance_mode: bool = False
    reboot_allowed: bool = False
    blocked: bool = False
    rollout_ring: str = "stable"
    force_downgrade: bool = False
    release_notes: str | None = None

    # Update window
    update_window_start: str | None = None
    update_window_end: str | None = None
    update_timezone: str = "UTC"
    allow_download_outside_window: bool = True
    allow_apply_outside_window: bool = False
    allow_reboot_outside_window: bool = False

    # Pending commands
    pending_commands: list[PendingCommandResponse] = Field(default_factory=list)


# ── State Report (agent → cloud) ─────────────────────────────────────


class StateReportRequest(BaseModel):
    gateway_id: uuid.UUID
    app_version: str | None = Field(None, max_length=50, pattern=r"^[A-Za-z0-9._+-]+$")
    config_version: str | None = Field(None, max_length=50, pattern=r"^[A-Za-z0-9._+-]+$")
    agent_version: str | None = Field(None, max_length=50, pattern=r"^[A-Za-z0-9._+-]+$")
    status: str = Field("idle", max_length=50, pattern=r"^[a-z][a-z0-9_]*$")
    health_status: str | None = Field(None, max_length=20, pattern=r"^[a-z][a-z0-9_]*$")
    disk_free_mb: int | None = Field(None, ge=0, le=1_000_000_000)
    cpu_temp_c: float | None = Field(None, ge=-50, le=150, allow_inf_nan=False)
    ram_usage_pct: float | None = Field(None, ge=0, le=100, allow_inf_nan=False)
    uptime_seconds: int | None = Field(None, ge=0, le=10_000_000_000)
    last_error: str | None = Field(None, max_length=2_000)
    update_download_status: str | None = Field(None, max_length=50, pattern=r"^[a-z][a-z0-9_]*$")
    update_apply_status: str | None = Field(None, max_length=50, pattern=r"^[a-z][a-z0-9_]*$")
    signature_status: str | None = Field(None, max_length=20, pattern=r"^[a-z][a-z0-9_]*$")


# ── Command Result (agent → cloud) ───────────────────────────────────


class CommandResultRequest(BaseModel):
    gateway_id: uuid.UUID
    command_id: uuid.UUID
    result: str = Field(pattern=r"^(executed|failed|rejected)$")
    message: str | None = Field(None, max_length=2_000)


# ── App Release ──────────────────────────────────────────────────────


class AppReleaseResponse(BaseModel):
    id: uuid.UUID
    version: str
    sha256: str
    signature: str | None = None
    mandatory: bool
    is_active: bool
    channel: str
    min_version: str | None = None
    file_size_bytes: int | None = None
    changelog: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AppReleaseListResponse(BaseModel):
    items: list[AppReleaseResponse]
    total: int


# ── Config Release ───────────────────────────────────────────────────


class ConfigReleaseCreate(BaseModel):
    version: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9._+-]+$")
    config_payload: dict
    schema_version: str = Field("1", min_length=1, max_length=20, pattern=r"^[A-Za-z0-9._-]+$")
    compatible_app_min: str | None = Field(None, max_length=50)
    compatible_app_max: str | None = Field(None, max_length=50)


class ConfigReleaseResponse(BaseModel):
    id: uuid.UUID
    version: str
    config_payload: dict
    schema_version: str
    compatible_app_min: str | None = None
    compatible_app_max: str | None = None
    sha256: str
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConfigReleaseListResponse(BaseModel):
    items: list[ConfigReleaseResponse]
    total: int


# ── Desired State Admin ─────────────────────────────────────────────


class DesiredStateUpdate(BaseModel):
    """Admin sets the target state for a gateway."""

    desired_app_version: str | None = Field(
        None,
        max_length=50,
        pattern=(
            r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
            r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
            r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
        ),
    )
    desired_config_version: str | None = Field(
        None,
        max_length=50,
        pattern=r"^[A-Za-z0-9._+-]+$",
    )
    maintenance_mode: bool | None = None
    reboot_allowed: bool | None = None
    blocked: bool | None = None
    rollout_ring: str | None = Field(None, pattern=r"^(canary|pilot|all|stable)$")
    force_downgrade: bool | None = None
    update_window_start: str | None = Field(None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    update_window_end: str | None = Field(None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    update_timezone: str | None = Field(None, min_length=1, max_length=50)
    allow_download_outside_window: bool | None = None
    allow_apply_outside_window: bool | None = None
    allow_reboot_outside_window: bool | None = None


# ── Commands Admin ───────────────────────────────────────────────────


ALLOWED_COMMAND_TYPES = {
    "restart_gateway_service",
    "reload_gateway_config",
    "enable_maintenance_mode",
    "disable_maintenance_mode",
    "controlled_reboot",
}


class CommandCreate(BaseModel):
    command_type: str = Field(min_length=1, max_length=50, description="Must be in allowlist")
    payload: dict | None = None


class CommandResponse(BaseModel):
    id: uuid.UUID
    gateway_id: uuid.UUID
    command_type: str
    payload: dict | None = None
    status: str
    created_at: datetime
    expires_at: datetime
    delivered_at: datetime | None = None
    executed_at: datetime | None = None
    result_message: str | None = None

    model_config = ConfigDict(from_attributes=True)


class CommandListResponse(BaseModel):
    items: list[CommandResponse]
    total: int


# ── Fleet Overview ───────────────────────────────────────────────────


class GatewayFleetItem(BaseModel):
    id: uuid.UUID
    hardware_id: str
    name: str | None = None
    zone_name: str | None = None
    status: str
    app_version: str | None = None
    config_version: str | None = None
    agent_version: str | None = None
    rollout_ring: str | None = None
    maintenance_mode: bool = False
    blocked: bool = False
    disk_free_mb: int | None = None
    disk_status: str | None = None  # ok / low / critical
    update_download_status: str | None = None
    update_apply_status: str | None = None
    signature_status: str | None = None
    update_window: str | None = None  # formatted string
    last_seen: datetime | None = None
    desired_app_version: str | None = None
    desired_config_version: str | None = None


class GatewayFleetResponse(BaseModel):
    items: list[GatewayFleetItem]
    total: int


# ── Update Logs ──────────────────────────────────────────────────────


class UpdateLogResponse(BaseModel):
    id: uuid.UUID
    gateway_id: uuid.UUID
    gateway_name: str | None = None
    update_type: str
    from_version: str | None = None
    to_version: str
    status: str
    error_message: str | None = None
    started_at: datetime
    completed_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class UpdateLogListResponse(BaseModel):
    items: list[UpdateLogResponse]
    total: int


# ── Rollout ──────────────────────────────────────────────────────────


class RolloutCreate(BaseModel):
    """Start a staged rollout of a gateway app release."""

    release_version: str = Field(
        min_length=1,
        max_length=50,
        pattern=(
            r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
            r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
            r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
        ),
    )
    target_ring: str = Field("canary", pattern=r"^(canary|pilot|all)$")
    zone_id: uuid.UUID | None = None


class RollbackRequest(BaseModel):
    """Trigger rollback to previous version for a gateway."""

    target_version: str | None = Field(
        None,
        max_length=50,
        pattern=(
            r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
            r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
            r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
        ),
    )  # None = auto-detect previous
