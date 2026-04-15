"""Gateway management request/response schemas."""

from pydantic import BaseModel, ConfigDict


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
    zone_id: str


class PairingCodeResponse(BaseModel):
    code: str
    expires_at: str
    zone_id: str


class RegisterGatewayRequest(BaseModel):
    code: str
    hardware_id: str
    name: str | None = None
    fw_version: str | None = None
    local_ip: str | None = None


class RegisterGatewayResponse(BaseModel):
    gateway_id: str
    api_key: str
    zone_id: str


class HeartbeatRequest(BaseModel):
    hardware_id: str
    local_ip: str | None = None
    cpu_temp_c: float | None = None
    ram_usage_pct: float | None = None
    wifi_rssi_dbm: int | None = None
    queue_depth: int | None = None


class GatewayDiscoveryRequest(BaseModel):
    mac_address: str
    code: str


class GatewayCommandResponse(BaseModel):
    action: str
    mac_address: str | None = None
