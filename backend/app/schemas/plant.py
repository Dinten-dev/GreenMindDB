"""Pydantic schemas for Plant management."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PlantCreate(BaseModel):
    zone_id: str
    name: str
    plant_code: str | None = None
    species: str | None = None
    cultivar: str | None = None
    description: str | None = None
    planted_at: datetime | None = None
    status: str = "active"


class PlantUpdate(BaseModel):
    name: str | None = None
    plant_code: str | None = None
    species: str | None = None
    cultivar: str | None = None
    description: str | None = None
    status: str | None = None


class PlantSensorAssignmentResponse(BaseModel):
    id: str
    plant_id: str
    sensor_id: str
    assigned_at: str
    unassigned_at: str | None = None
    notes: str | None = None
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class PlantResponse(BaseModel):
    id: str
    organization_id: str
    zone_id: str
    name: str
    plant_code: str | None = None
    species: str | None = None
    cultivar: str | None = None
    description: str | None = None
    planted_at: str | None = None
    status: str
    created_at: str
    updated_at: str
    current_sensor_id: str | None = None

    model_config = ConfigDict(from_attributes=True)


class AssignSensorRequest(BaseModel):
    sensor_id: str
    notes: str | None = None
