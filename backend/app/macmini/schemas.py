from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class PlantSignalIngestRequest(BaseModel):
    plant_id: UUID
    sensor_id: UUID
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
    created_by: str | None
    source_device_id: UUID | None


class ExportRequest(BaseModel):
    greenhouse_id: UUID
    plant_ids: list[UUID] | None = None
    from_time: datetime = Field(alias="from")
    to_time: datetime = Field(alias="to")
    include_env: bool = True
    include_events: bool = True
    resample: Literal["raw", "1m", "15m"] = "raw"


class ExportResponse(BaseModel):
    export_id: UUID


class ExportStatusResponse(BaseModel):
    export_id: UUID
    status: Literal["running", "completed", "failed"]
    storage_key: str | None = None
    error: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    db: Literal["ok", "error"]
    minio: Literal["ok", "error"]


class ErrorResponse(BaseModel):
    detail: str
