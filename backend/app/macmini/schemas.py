"""Pydantic request/response schemas for all API endpoints."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# ═══════════════════════════════════════════════════════════════
# Auth
# ═══════════════════════════════════════════════════════════════

class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    role: str
    greenhouse_id: UUID | None = None


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


# ═══════════════════════════════════════════════════════════════
# Users (admin)
# ═══════════════════════════════════════════════════════════════

class UserCreate(BaseModel):
    email: str
    password: str = Field(min_length=8)
    role: Literal["admin", "operator", "research", "viewer"] = "operator"
    greenhouse_id: UUID | None = None


class UserUpdate(BaseModel):
    role: Literal["admin", "operator", "research", "viewer"] | None = None
    greenhouse_id: UUID | None = None
    is_active: bool | None = None


class UserOut(BaseModel):
    id: UUID
    email: str
    role: str
    greenhouse_id: UUID | None
    is_active: bool
    created_at: datetime
    last_login: datetime | None

    model_config = {"from_attributes": True}


# ═══════════════════════════════════════════════════════════════
# Master data
# ═══════════════════════════════════════════════════════════════

class GreenhouseCreate(BaseModel):
    name: str
    location: str | None = None
    timezone: str = "UTC"


class GreenhouseOut(BaseModel):
    id: UUID
    name: str
    location: str | None
    timezone: str
    model_config = {"from_attributes": True}


class ZoneCreate(BaseModel):
    greenhouse_id: UUID
    name: str


class ZoneOut(BaseModel):
    id: UUID
    greenhouse_id: UUID
    name: str
    model_config = {"from_attributes": True}


class PlantCreate(BaseModel):
    zone_id: UUID
    species: str
    cultivar: str | None = None
    planted_at: datetime | None = None
    tags: dict[str, Any] = Field(default_factory=dict)


class PlantOut(BaseModel):
    id: UUID
    zone_id: UUID
    species: str
    cultivar: str | None
    planted_at: datetime | None
    tags: dict[str, Any]
    model_config = {"from_attributes": True}


class DeviceCreate(BaseModel):
    greenhouse_id: UUID
    serial: str
    type: str
    fw_version: str | None = None


class DeviceOut(BaseModel):
    id: UUID
    greenhouse_id: UUID
    serial: str
    type: str
    fw_version: str | None
    last_seen: datetime | None
    status: str
    model_config = {"from_attributes": True}


class DeviceKeyResponse(BaseModel):
    device_id: UUID
    api_key: str


class GreenhouseSummary(BaseModel):
    greenhouse_id: UUID
    name: str
    device_count: int
    plant_count: int
    active_device_count: int
    last_seen: datetime | None


class SensorCreate(BaseModel):
    device_id: UUID
    plant_id: UUID | None = None
    zone_id: UUID | None = None
    kind: str
    unit: str
    calibration: dict[str, Any] = Field(default_factory=dict)


class SensorOut(BaseModel):
    id: UUID
    device_id: UUID
    plant_id: UUID | None
    zone_id: UUID | None
    kind: str
    unit: str
    calibration: dict[str, Any]
    calibration: dict[str, Any]
    model_config = {"from_attributes": True}


class DeviceLiveData(BaseModel):
    device_id: UUID
    timestamp: datetime
    sensors: dict[str, dict[str, Any]]  # sensor_id -> {value, time, kind, quality}


# ═══════════════════════════════════════════════════════════════
# Ingestion
# ═══════════════════════════════════════════════════════════════

class PlantSignalIngestRequest(BaseModel):
    plant_id: UUID
    sensor_id: UUID
    greenhouse_id: UUID
    start_time: datetime
    dt_seconds: int = Field(default=1, ge=1, le=300)
    values_uV: list[float] = Field(min_length=1)
    quality: list[int] | None = None
    request_id: UUID

    @field_validator("quality")
    @classmethod
    def validate_quality(cls, v: list[int] | None, info):
        if v is None:
            return v
        values = info.data.get("values_uV", [])
        if len(v) != len(values):
            raise ValueError("quality length must match values_uV length")
        return v


class EnvMeasurementItem(BaseModel):
    sensor_id: UUID
    greenhouse_id: UUID
    time: datetime
    value: float
    quality: int = 0
    meta: dict[str, Any] = Field(default_factory=dict)


class EnvIngestRequest(BaseModel):
    request_id: UUID
    measurements: list[EnvMeasurementItem] = Field(min_length=1)


class EventIngestRequest(BaseModel):
    request_id: UUID
    time: datetime
    greenhouse_id: UUID
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class IngestResponse(BaseModel):
    request_id: UUID
    status: Literal["ingested", "duplicate"]
    inserted_rows: int


# ═══════════════════════════════════════════════════════════════
# Query results
# ═══════════════════════════════════════════════════════════════

class SignalPointRaw(BaseModel):
    time: datetime
    plant_id: UUID
    sensor_id: UUID
    value_uV: float
    quality: int
    meta: dict[str, Any]


class SignalPointAgg(BaseModel):
    bucket: datetime
    plant_id: UUID
    sensor_id: UUID
    value_avg: float
    value_min: float
    value_max: float
    sample_count: int


class EnvPointRaw(BaseModel):
    time: datetime
    sensor_id: UUID
    value: float
    quality: int
    meta: dict[str, Any]


class EnvPointAgg(BaseModel):
    bucket: datetime
    sensor_id: UUID
    value_avg: float
    value_min: float
    value_max: float
    sample_count: int


class EventOut(BaseModel):
    id: UUID
    greenhouse_id: UUID
    time: datetime
    type: str
    payload: dict[str, Any]
    created_by_user_id: UUID | None
    source_device_id: UUID | None
    model_config = {"from_attributes": True}


# ═══════════════════════════════════════════════════════════════
# Ground Truth
# ═══════════════════════════════════════════════════════════════

class GroundTruthCreate(BaseModel):
    plant_id: UUID
    date: date
    vitality_score: int = Field(ge=1, le=5)
    growth_score: int = Field(ge=1, le=5)
    pest_score: int = Field(ge=1, le=5)
    disease_score: int = Field(ge=1, le=5)
    notes: str | None = None


class GroundTruthOut(BaseModel):
    id: UUID
    greenhouse_id: UUID
    plant_id: UUID
    date: date
    vitality_score: int
    growth_score: int
    pest_score: int
    disease_score: int
    notes: str | None
    created_by_user_id: UUID | None
    created_at: datetime
    model_config = {"from_attributes": True}


# ═══════════════════════════════════════════════════════════════
# Annotations / Labels
# ═══════════════════════════════════════════════════════════════

class LabelSchemaCreate(BaseModel):
    greenhouse_id: UUID | None = None
    name: str
    description: str | None = None
    values: list[str]
    version: int = 1


class LabelSchemaOut(BaseModel):
    id: UUID
    greenhouse_id: UUID | None
    name: str
    description: str | None
    values: list[str] | Any
    version: int
    is_active: bool
    model_config = {"from_attributes": True}


class AnnotationCreate(BaseModel):
    plant_id: UUID
    sensor_id: UUID | None = None
    start_time: datetime
    end_time: datetime
    label_key: str
    label_value: str
    confidence: int | None = None
    notes: str | None = None


class AnnotationOut(BaseModel):
    id: UUID
    greenhouse_id: UUID
    plant_id: UUID
    sensor_id: UUID | None
    start_time: datetime
    end_time: datetime
    label_key: str
    label_value: str
    confidence: int | None
    notes: str | None
    created_by_user_id: UUID | None
    created_at: datetime
    status: str
    model_config = {"from_attributes": True}


class AnnotationReviewCreate(BaseModel):
    decision: Literal["approve", "reject"]
    notes: str | None = None


class AnnotationReviewOut(BaseModel):
    id: UUID
    annotation_id: UUID
    reviewed_by_user_id: UUID | None
    decision: str
    notes: str | None
    reviewed_at: datetime
    model_config = {"from_attributes": True}


# ═══════════════════════════════════════════════════════════════
# Media
# ═══════════════════════════════════════════════════════════════

class PresignRequest(BaseModel):
    greenhouse_id: UUID
    kind: str
    content_type: str
    plant_id: UUID | None = None
    zone_id: UUID | None = None


class PresignResponse(BaseModel):
    upload_url: str
    storage_key: str


class MediaCommitRequest(BaseModel):
    storage_key: str
    greenhouse_id: UUID
    kind: str
    content_type: str
    sha256: str | None = None
    size_bytes: int | None = None
    plant_id: UUID | None = None
    zone_id: UUID | None = None


class ObjectMetaOut(BaseModel):
    id: UUID
    time: datetime
    greenhouse_id: UUID
    kind: str
    storage_key: str
    content_type: str
    sha256: str | None
    size_bytes: int | None
    plant_id: UUID | None
    zone_id: UUID | None
    created_by_user_id: UUID | None
    model_config = {"from_attributes": True}


# ═══════════════════════════════════════════════════════════════
# Export
# ═══════════════════════════════════════════════════════════════

class ExportRequest(BaseModel):
    greenhouse_id: UUID
    plant_ids: list[UUID] | None = None
    from_time: datetime = Field(alias="from")
    to_time: datetime = Field(alias="to")
    include_env: bool = True
    include_events: bool = True
    include_ground_truth: bool = True
    include_annotations: bool = True
    resample: Literal["raw", "1m", "15m"] = "raw"

    model_config = {"populate_by_name": True}


class ExportResponse(BaseModel):
    export_id: UUID


class ExportStatusResponse(BaseModel):
    export_id: UUID
    status: Literal["running", "completed", "failed"]
    storage_key: str | None = None
    row_count: int | None = None
    error: dict | None = None
    created_at: datetime


# ═══════════════════════════════════════════════════════════════
# Health / Audit
# ═══════════════════════════════════════════════════════════════

class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    db: Literal["ok", "error"]
    minio: Literal["ok", "error"]


class AuditLogOut(BaseModel):
    id: UUID
    time: datetime
    actor_user_id: UUID | None
    actor_type: str
    action: str
    resource_type: str | None
    resource_id: str | None
    greenhouse_id: UUID | None
    ip: str | None
    details: dict[str, Any]
    model_config = {"from_attributes": True}


class ErrorResponse(BaseModel):
    detail: str

# ═══════════════════════════════════════════════════════════════
# Ingest Logs
# ═══════════════════════════════════════════════════════════════

class IngestLogOut(BaseModel):
    request_id: UUID
    received_at: datetime
    endpoint: str
    source: str | None
    status: str
    details: dict[str, Any]
    model_config = {"from_attributes": True}
