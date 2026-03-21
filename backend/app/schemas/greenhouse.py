"""Greenhouse request/response schemas."""

from pydantic import BaseModel


class GreenhouseCreate(BaseModel):
    name: str
    location: str | None = None


class GreenhouseResponse(BaseModel):
    id: str
    name: str
    location: str | None = None
    created_at: str
    device_count: int = 0
    sensor_count: int = 0

    class Config:
        from_attributes = True


class GreenhouseOverview(BaseModel):
    id: str
    name: str
    total_devices: int
    online_devices: int
    total_sensors: int
    readings_24h: int
