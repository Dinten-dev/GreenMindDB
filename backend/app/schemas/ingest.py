"""Ingestion request/response schemas."""

from pydantic import BaseModel


class ReadingPayload(BaseModel):
    sensor_kind: str
    value: float
    unit: str
    timestamp: str | None = None


class IngestRequest(BaseModel):
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
