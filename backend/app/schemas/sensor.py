"""Sensor (ESP32) request/response schemas."""

from pydantic import BaseModel


class SensorResponse(BaseModel):
    id: str
    gateway_id: str
    zone_id: str | None = None
    mac_address: str
    name: str | None = None
    sensor_type: str
    status: str
    last_seen: str | None = None
    claimed_at: str | None = None
    gateway_name: str | None = None
    gateway_hardware_id: str | None = None

    class Config:
        from_attributes = True


class ClaimSensorRequest(BaseModel):
    mac_address: str
    sensor_type: str = "generic"
    name: str | None = None


class PairSensorRequest(BaseModel):
    code: str
    zone_id: str
    name: str | None = None


class ClaimSensorResponse(BaseModel):
    sensor_id: str
    mac_address: str
    gateway_id: str


class MoveSensorRequest(BaseModel):
    target_gateway_id: str


class DataPoint(BaseModel):
    timestamp: str
    value: float


class SensorDataResponse(BaseModel):
    sensor_id: str
    kind: str
    unit: str
    data: list[DataPoint]
