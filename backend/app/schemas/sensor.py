"""Sensor (ESP32) request/response schemas."""

import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.validation import normalize_mac_address


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
    sms_alerts_enabled: bool = True

    model_config = ConfigDict(from_attributes=True)


class SensorUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    sms_alerts_enabled: bool | None = None


class ClaimSensorRequest(BaseModel):
    mac_address: str = Field(min_length=12, max_length=17)
    sensor_type: str = Field("generic", min_length=1, max_length=50, pattern=r"^[A-Za-z0-9_.:-]+$")
    name: str | None = Field(None, min_length=1, max_length=200)

    @field_validator("mac_address")
    @classmethod
    def validate_mac(cls, value: str) -> str:
        return normalize_mac_address(value)


class PairSensorRequest(BaseModel):
    code: str = Field(min_length=6, max_length=8, pattern=r"^[A-Za-z0-9]+$")
    zone_id: uuid.UUID
    name: str | None = Field(None, min_length=1, max_length=200)

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return value.upper()


class ClaimSensorResponse(BaseModel):
    sensor_id: str
    mac_address: str
    gateway_id: str


class MoveSensorRequest(BaseModel):
    target_gateway_id: uuid.UUID


class DataPoint(BaseModel):
    timestamp: str
    value: float


class SensorDataResponse(BaseModel):
    sensor_id: str
    kind: str
    unit: str
    data: list[DataPoint]
