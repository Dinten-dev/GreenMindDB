"""Ingestion request/response schemas."""

import uuid
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.validation import normalize_mac_address


class ReadingPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    sensor_mac: str = Field(min_length=12, max_length=17)
    sensor_kind: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")
    value: float = Field(allow_inf_nan=False)
    unit: str = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9%°µ/_.*+-]+$")
    timestamp: datetime | None = None
    source_sequence: int | None = Field(None, ge=0, le=4_294_967_295)
    source_uptime_ms: int | None = Field(None, ge=0, le=4_294_967_295)
    source_dropped_samples_total: int | None = Field(None, ge=0, le=4_294_967_295)
    sample_count: int | None = Field(None, ge=1, le=100_000)
    sample_rate_hz: float | None = Field(None, gt=0, le=100_000, allow_inf_nan=False)
    median: float | None = Field(None, allow_inf_nan=False)
    rms: float | None = Field(None, ge=0, allow_inf_nan=False)
    standard_deviation: float | None = Field(None, ge=0, allow_inf_nan=False)
    minimum: float | None = Field(None, allow_inf_nan=False)
    maximum: float | None = Field(None, allow_inf_nan=False)
    p05: float | None = Field(None, allow_inf_nan=False)
    p95: float | None = Field(None, allow_inf_nan=False)
    coverage_ratio: float | None = Field(None, ge=0, le=1, allow_inf_nan=False)
    source_boot_id: int | None = Field(None, ge=0, le=4_294_967_295)
    protocol_version: int | None = Field(None, ge=1, le=100)
    firmware_version: str | None = Field(None, min_length=1, max_length=50)
    calibration_version: str | None = Field(None, min_length=1, max_length=50)
    quality_valid_count: int | None = Field(None, ge=0, le=100_000)
    quality_lead_off_count: int | None = Field(None, ge=0, le=100_000)
    quality_rail_high_count: int | None = Field(None, ge=0, le=100_000)
    quality_rail_low_count: int | None = Field(None, ge=0, le=100_000)
    quality_jump_count: int | None = Field(None, ge=0, le=100_000)
    quality_recovery_count: int | None = Field(None, ge=0, le=100_000)

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

    @model_validator(mode="after")
    def validate_aggregate(self) -> "ReadingPayload":
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum must not exceed maximum")
        if self.p05 is not None and self.p95 is not None and self.p05 > self.p95:
            raise ValueError("p05 must not exceed p95")
        if self.sample_count is not None:
            counters = (
                self.quality_valid_count,
                self.quality_lead_off_count,
                self.quality_rail_high_count,
                self.quality_rail_low_count,
                self.quality_jump_count,
                self.quality_recovery_count,
            )
            if any(value is not None and value > self.sample_count for value in counters):
                raise ValueError("quality counter exceeds sample_count")
        return self


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
