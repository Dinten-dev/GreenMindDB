"""Gateway management request/response schemas."""

import ipaddress
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.validation import normalize_mac_address


class GatewayResponse(BaseModel):
    id: str
    zone_id: str
    zone_name: str | None = None
    hardware_id: str
    name: str | None = None
    local_ip: str | None = None
    fw_version: str | None = None
    status: str
    is_active: bool = True
    last_seen: str | None = None
    paired_at: str | None = None
    sensor_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class PairingCodeRequest(BaseModel):
    zone_id: uuid.UUID


class PairingCodeResponse(BaseModel):
    code: str
    expires_at: str
    zone_id: str


class RegisterGatewayRequest(BaseModel):
    code: str = Field(min_length=6, max_length=8, pattern=r"^[A-Za-z0-9]+$")
    hardware_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$")
    name: str | None = Field(None, max_length=200)
    fw_version: str | None = Field(None, max_length=50, pattern=r"^[A-Za-z0-9._+-]+$")
    local_ip: str | None = Field(None, max_length=45)

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return value.upper()

    @field_validator("local_ip")
    @classmethod
    def validate_local_ip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return str(ipaddress.ip_address(value))


class RegisterGatewayResponse(BaseModel):
    gateway_id: str
    api_key: str
    zone_id: str


class HeartbeatRequest(BaseModel):
    hardware_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$")
    local_ip: str | None = Field(None, max_length=45)
    cpu_temp_c: float | None = Field(None, ge=-50, le=150, allow_inf_nan=False)
    ram_usage_pct: float | None = Field(None, ge=0, le=100, allow_inf_nan=False)
    wifi_rssi_dbm: int | None = Field(None, ge=-150, le=0)
    queue_depth: int | None = Field(None, ge=0, le=10_000_000)

    @field_validator("local_ip")
    @classmethod
    def validate_local_ip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return str(ipaddress.ip_address(value))


class GatewayDiscoveryRequest(BaseModel):
    mac_address: str = Field(min_length=12, max_length=17)
    code: str = Field(min_length=6, max_length=8, pattern=r"^[A-Za-z0-9]+$")

    @field_validator("mac_address")
    @classmethod
    def validate_mac(cls, value: str) -> str:
        return normalize_mac_address(value)

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return value.upper()


class GatewayCommandResponse(BaseModel):
    action: str
    mac_address: str | None = None
