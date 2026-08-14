"""Pydantic schemas for Plant Observation & Public QR Access."""

from pydantic import BaseModel, ConfigDict


class ObservationAccessResponse(BaseModel):
    id: str
    plant_id: str
    public_id: str
    is_active: bool
    usage_count: int
    revoked_at: str | None = None

    model_config = ConfigDict(from_attributes=True)


class ObservationSessionCreate(BaseModel):
    public_id: str


class ObservationSessionResponse(BaseModel):
    session_token: str
    expires_at: str

    model_config = ConfigDict(from_attributes=True)


class PublicPlantContextResponse(BaseModel):
    plant_id: str
    name: str
    plant_code: str | None = None
    zone_name: str | None = None

    model_config = ConfigDict(from_attributes=True)


class PlantObservationCreate(BaseModel):
    wellbeing_score: int
    stress_score: int | None = None
    plant_condition: str
    leaf_droop: bool | None = None
    leaf_color: str | None = None
    spots_present: bool | None = None
    soil_condition: str | None = None
    suspected_stress_type: str | None = None
    notes: str | None = None


class PlantObservationPhotoResponse(BaseModel):
    id: str
    observation_id: str
    storage_key: str
    mime_type: str
    file_size: int
    created_at: str

    model_config = ConfigDict(from_attributes=True)


class PlantObservationResponse(BaseModel):
    id: str
    plant_id: str
    sensor_id: str | None = None
    zone_id: str
    observed_at: str
    wellbeing_score: int
    stress_score: int | None = None
    plant_condition: str
    leaf_droop: bool | None = None
    leaf_color: str | None = None
    spots_present: bool | None = None
    soil_condition: str | None = None
    suspected_stress_type: str | None = None
    notes: str | None = None
    created_at: str
    photos: list[PlantObservationPhotoResponse] = []

    model_config = ConfigDict(from_attributes=True)
