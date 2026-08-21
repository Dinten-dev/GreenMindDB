"""Ingestion request/response schemas."""

import uuid
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.validation import normalize_mac_address


class ReadingPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    sensor_mac: str = Field(min_length=12, max_length=17)
    sensor_kind: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")
    value: float = Field(allow_inf_nan=False)
    unit: str = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9%°µ/_.*+-]+$")
    timestamp: datetime | None = None

    @field_validator("sensor_mac")
    @classmethod
    def validate_mac(cls, value: str) -> str:
        return normalize_mac_address(value)

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        if value > datetime.now(UTC) + timedelta(hours=24):
            raise ValueError("Reading timestamp is too far in the future")
        return value


class IngestRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    measurement_id: uuid.UUID
    gateway_serial: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    aggregation_window: str | None = Field(
        None,
        max_length=50,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    )
    sampling_rate_hz: float | None = Field(None, gt=0, le=100_000, allow_inf_nan=False)
    checksum: str | None = Field(None, pattern=r"^[0-9A-Fa-f]{64}$")
    raw_file_reference: str | None = Field(None, max_length=500)
    readings: list[ReadingPayload] = Field(max_length=5_000)

    @field_validator("raw_file_reference")
    @classmethod
    def validate_raw_reference(cls, value: str | None) -> str | None:
        if value is not None and (value.startswith(("/", "\\")) or ".." in value):
            raise ValueError("raw_file_reference must be a relative, contained identifier")
        return value


class IngestResponse(BaseModel):
    status: str
    ingested: int
    gateway_id: str
    measurement_id: str
