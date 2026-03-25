"""Gateway management request/response schemas."""

from pydantic import BaseModel


class GatewayResponse(BaseModel):
    id: str
    greenhouse_id: str
    greenhouse_name: str | None = None
    hardware_id: str
    name: str | None = None
    local_ip: str | None = None
    fw_version: str | None = None
    status: str
    is_active: bool = True
    last_seen: str | None = None
    paired_at: str | None = None
    sensor_count: int = 0

    class Config:
        from_attributes = True


class PairingCodeRequest(BaseModel):
    greenhouse_id: str


class PairingCodeResponse(BaseModel):
    code: str
    expires_at: str
    greenhouse_id: str


class RegisterGatewayRequest(BaseModel):
    code: str
    hardware_id: str
    name: str | None = None
    fw_version: str | None = None
    local_ip: str | None = None


class RegisterGatewayResponse(BaseModel):
    gateway_id: str
    api_key: str
    greenhouse_id: str


class HeartbeatRequest(BaseModel):
    hardware_id: str
    local_ip: str | None = None
