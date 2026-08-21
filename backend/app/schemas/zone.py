"""Zone (agriculture area) request/response schemas."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ZoneCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    location: str | None = Field(None, max_length=500)
    zone_type: Literal["GREENHOUSE", "OPEN_FIELD", "VERTICAL_FARM", "ORCHARD"] = "GREENHOUSE"
    latitude: float | None = Field(None, ge=-90, le=90, allow_inf_nan=False)
    longitude: float | None = Field(None, ge=-180, le=180, allow_inf_nan=False)


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

    model_config = ConfigDict(from_attributes=True)


class ZoneOverview(BaseModel):
    id: str
    name: str
    zone_type: str
    total_gateways: int
    online_gateways: int
    total_sensors: int
    readings_24h: int
