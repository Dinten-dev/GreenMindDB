"""Ingestion request/response schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ReadingPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    sensor_kind: str
    value: float
    unit: str
    timestamp: datetime | None = None


class IngestRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    measurement_id: str
    device_serial: str
    aggregation_window: str | None = None
    sampling_rate_hz: float | None = None
    checksum: str | None = None
    raw_file_reference: str | None = None
    readings: list[ReadingPayload]


class IngestResponse(BaseModel):
    status: str
    ingested: int
    device_id: str
    measurement_id: str
