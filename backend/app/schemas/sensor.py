"""Sensor data request/response schemas."""

from pydantic import BaseModel


class SensorResponse(BaseModel):
    id: str
    device_id: str
    kind: str
    unit: str
    label: str | None = None
    device_serial: str | None = None
    device_name: str | None = None
    device_status: str | None = None


class DataPoint(BaseModel):
    timestamp: str
    value: float


class SensorDataResponse(BaseModel):
    sensor_id: str
    kind: str
    unit: str
    label: str | None = None
    data: list[DataPoint]
