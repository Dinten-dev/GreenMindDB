"""Firmware schemas: Request/Response models for OTA updates and admin UI."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.validation import normalize_mac_address

# ── Base & Create ────────────────────────────────────────────────────


class FirmwareReleaseBase(BaseModel):
    version: str = Field(
        min_length=1,
        max_length=50,
        description="Semantic version string, e.g., 1.0.0",
    )
    board_type: str = Field(
        min_length=1,
        max_length=50,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="ESP32 variant, e.g., ESP32_WROOM",
    )
    hardware_revision: str = Field(
        min_length=1,
        max_length=50,
        pattern=r"^[A-Za-z0-9._-]+$",
        description="e.g., v1",
    )
    mandatory: bool = False
    min_version: str | None = None
    changelog: str | None = None


class FirmwareReleaseCreate(FirmwareReleaseBase):
    pass


# ── Responses ────────────────────────────────────────────────────────


class FirmwareReleaseResponse(FirmwareReleaseBase):
    id: uuid.UUID
    file_path: str
    sha256: str
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FirmwareSyncResponse(BaseModel):
    """Returned to gateway when checking for updates."""

    id: uuid.UUID
    version: str
    board_type: str
    hardware_revision: str
    firmware_url: str
    sha256: str
    mandatory: bool
    min_version: str | None = None
    changelog: str | None = None


# ── Paginated list ───────────────────────────────────────────────────


class PaginationMeta(BaseModel):
    total: int
    offset: int
    limit: int


class FirmwareReleaseListResponse(BaseModel):
    items: list[FirmwareReleaseResponse]
    meta: PaginationMeta


# ── Reports ──────────────────────────────────────────────────────────


class FirmwareReportRequest(BaseModel):
    sensor_mac: str | None = Field(
        None,
        min_length=12,
        max_length=17,
        description="MAC of sensor updating",
    )
    release_id: uuid.UUID
    status: str = Field(
        ...,
        pattern=r"^(success|failed|hash_mismatch|rollback|incompatible)$",
        description="Firmware update result",
    )
    error_message: str | None = Field(None, max_length=1_000)

    @field_validator("sensor_mac")
    @classmethod
    def validate_sensor_mac(cls, value: str | None) -> str | None:
        return normalize_mac_address(value) if value is not None else None


class FirmwareReportResponse(BaseModel):
    id: uuid.UUID
    sensor_id: uuid.UUID | None
    gateway_id: uuid.UUID
    release_id: uuid.UUID
    status: str
    error_message: str | None
    reported_at: datetime
    release_version: str | None = None
    gateway_name: str | None = None

    model_config = ConfigDict(from_attributes=True)


class FirmwareReportListResponse(BaseModel):
    items: list[FirmwareReportResponse]
    meta: PaginationMeta


# ── Rollout Policies ─────────────────────────────────────────────────


class RolloutPolicyCreate(BaseModel):
    release_id: uuid.UUID
    zone_id: uuid.UUID | None = None
    canary_percentage: str = Field("100", pattern=r"^(?:100|[1-9]?\d)$")


class RolloutPolicyResponse(RolloutPolicyCreate):
    id: uuid.UUID
    created_at: datetime
    release_version: str | None = None
    zone_name: str | None = None

    model_config = ConfigDict(from_attributes=True)


# ── Dashboard ────────────────────────────────────────────────────────


class DashboardSummary(BaseModel):
    active_releases: int
    total_releases: int
    online_gateways: int
    total_gateways: int
    total_devices: int
    failed_updates_24h: int
    successful_updates_24h: int
    active_rollouts: int


# ── Audit ────────────────────────────────────────────────────────────


class AuditLogResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID | None
    user_email: str | None = None
    action: str
    entity_type: str
    entity_id: str | None
    details: str | None
    ip_address: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuditLogListResponse(BaseModel):
    items: list[AuditLogResponse]
    meta: PaginationMeta
