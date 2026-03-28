"""Zone (agriculture area) request/response schemas."""

from pydantic import BaseModel


class ZoneCreate(BaseModel):
    name: str
    location: str | None = None
    zone_type: str = "GREENHOUSE"
    latitude: float | None = None
    longitude: float | None = None


class ZoneResponse(BaseModel):
    id: str
    name: str
    location: str | None = None
    zone_type: str = "GREENHOUSE"
    latitude: float | None = None
    longitude: float | None = None
    created_at: str
    gateway_count: int = 0
    sensor_count: int = 0

    class Config:
        from_attributes = True


class ZoneOverview(BaseModel):
    id: str
    name: str
    zone_type: str
    total_gateways: int
    online_gateways: int
    total_sensors: int
    readings_24h: int
