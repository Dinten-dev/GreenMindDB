"""Device management request/response schemas."""

from pydantic import BaseModel


class DeviceResponse(BaseModel):
    id: str
    serial: str
    name: str | None = None
    type: str
    fw_version: str | None = None
    status: str
    last_seen: str | None = None
    greenhouse_id: str | None = None
    greenhouse_name: str | None = None
    sensor_count: int = 0
    paired_at: str | None = None

    class Config:
        from_attributes = True


class PairingCodeRequest(BaseModel):
    greenhouse_id: str


class PairingCodeResponse(BaseModel):
    code: str
    expires_at: str
    greenhouse_id: str


class PairDeviceRequest(BaseModel):
    code: str
    serial: str
    type: str = "esp32"
    name: str | None = None
    fw_version: str | None = None


class PairDeviceResponse(BaseModel):
    device_id: str
    api_key: str
    greenhouse_id: str
