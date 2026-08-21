"""Pydantic schemas for structured plant evaluations."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator


class LeafColor(StrEnum):
    SATURATED_GREEN = "saturated_green"
    LIGHT_GREEN = "light_green"
    YELLOWISH = "yellowish"
    SPOTTED = "spotted"
    BROWN_DEAD = "brown_dead"


class LeafStructure(StrEnum):
    FIRM_TAUT = "firm_taut"
    SLIGHTLY_LIMP = "slightly_limp"
    VERY_LIMP = "very_limp"
    CURLED_DEFORMED = "curled_deformed"


class GrowthState(StrEnum):
    STRONG = "strong"
    NORMAL = "normal"
    SLOW = "slow"
    NONE = "none"


class WaterState(StrEnum):
    TOO_DRY = "too_dry"
    OPTIMAL = "optimal"
    TOO_WET = "too_wet"


class Anomaly(StrEnum):
    SPOTS = "spots"
    HOLES = "holes"
    MOLD = "mold"
    PESTS = "pests"
    NONE = "none"


class PlantEvaluationCreate(BaseModel):
    """Request body for creating a plant evaluation."""

    overall_score: int
    color_raw: LeafColor
    structure_raw: LeafStructure
    growth_raw: GrowthState
    water_raw: WaterState
    anomalies_raw: list[Anomaly]
    detail_notes: str | None = None

    @field_validator("overall_score")
    @classmethod
    def validate_overall_score(cls, value: int) -> int:
        if not 1 <= value <= 5:
            raise ValueError("overall_score must be between 1 and 5")
        return value

    @field_validator("anomalies_raw")
    @classmethod
    def validate_anomalies(cls, value: list[Anomaly]) -> list[Anomaly]:
        if not value:
            raise ValueError("At least one anomaly option must be selected")
        if Anomaly.NONE in value and len(value) > 1:
            raise ValueError("'none' cannot be combined with other anomalies")
        return value


class PlantEvaluationResponse(BaseModel):
    """Response body for a plant evaluation."""

    id: str
    plant_id: str
    sensor_id: str | None = None
    zone_id: str
    evaluated_at: str
    overall_score: int
    color_score: int
    structure_score: int
    growth_score: int
    water_score: int
    anomalies_vector: int
    color_raw: str
    structure_raw: str
    growth_raw: str
    water_raw: str
    anomalies_raw: str
    confidence_score: float | None = None
    created_at: str

    model_config = ConfigDict(from_attributes=True)
