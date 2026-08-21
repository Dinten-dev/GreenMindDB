"""Pydantic schemas for Plant management."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PlantCreate(BaseModel):
    zone_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)
    plant_code: str | None = Field(None, max_length=100)
    species: str | None = Field(None, max_length=200)
    cultivar: str | None = Field(None, max_length=200)
    description: str | None = Field(None, max_length=10_000)
    planted_at: datetime | None = None
    status: str = Field("active", pattern=r"^(?:active|archived|removed)$")


class PlantUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    plant_code: str | None = Field(None, max_length=100)
    species: str | None = Field(None, max_length=200)
    cultivar: str | None = Field(None, max_length=200)
    description: str | None = Field(None, max_length=10_000)
    status: str | None = Field(None, pattern=r"^(?:active|archived|removed)$")


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
    sensor_id: uuid.UUID
    notes: str | None = Field(None, max_length=2_000)
