"""Ingestion request/response schemas."""

from pydantic import BaseModel


class ReadingPayload(BaseModel):
    sensor_kind: str
    value: float
    unit: str
    timestamp: str | None = None


class IngestRequest(BaseModel):
    device_serial: str
    readings: list[ReadingPayload]


class IngestResponse(BaseModel):
    ingested: int
    device_id: str
