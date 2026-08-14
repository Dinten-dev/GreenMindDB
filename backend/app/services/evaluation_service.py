"""Business logic for structured plant evaluations.

Handles score mapping, anomaly bitmask encoding, confidence computation,
and persistence of ML-ready evaluation records.
"""

import logging
from typing import Final

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.evaluation import PlantEvaluation
from app.models.plant import Plant, PlantSensorAssignment
from app.schemas.evaluation import (
    Anomaly,
    GrowthState,
    LeafColor,
    LeafStructure,
    PlantEvaluationCreate,
    PlantEvaluationResponse,
    WaterState,
)
from app.services.observation_service import _get_valid_session

logger = logging.getLogger(__name__)

# ── Score Mapping Tables ─────────────────────────────────────────────

COLOR_SCORES: Final[dict[LeafColor, int]] = {
    LeafColor.SATURATED_GREEN: 5,
    LeafColor.LIGHT_GREEN: 4,
    LeafColor.YELLOWISH: 3,
    LeafColor.SPOTTED: 2,
    LeafColor.BROWN_DEAD: 1,
}

STRUCTURE_SCORES: Final[dict[LeafStructure, int]] = {
    LeafStructure.FIRM_TAUT: 5,
    LeafStructure.SLIGHTLY_LIMP: 3,
    LeafStructure.VERY_LIMP: 2,
    LeafStructure.CURLED_DEFORMED: 1,
}

GROWTH_SCORES: Final[dict[GrowthState, int]] = {
    GrowthState.STRONG: 5,
    GrowthState.NORMAL: 4,
    GrowthState.SLOW: 2,
    GrowthState.NONE: 1,
}

WATER_SCORES: Final[dict[WaterState, int]] = {
    WaterState.TOO_DRY: 2,
    WaterState.OPTIMAL: 5,
    WaterState.TOO_WET: 2,
}

ANOMALY_BITS: Final[dict[Anomaly, int]] = {
    Anomaly.SPOTS: 1,
    Anomaly.HOLES: 2,
    Anomaly.MOLD: 4,
    Anomaly.PESTS: 8,
    Anomaly.NONE: 16,
}


# ── Pure Mapping Functions ───────────────────────────────────────────


def map_color_score(raw: LeafColor) -> int:
    return COLOR_SCORES[raw]


def map_structure_score(raw: LeafStructure) -> int:
    return STRUCTURE_SCORES[raw]


def map_growth_score(raw: GrowthState) -> int:
    return GROWTH_SCORES[raw]


def map_water_score(raw: WaterState) -> int:
    return WATER_SCORES[raw]


def encode_anomalies(raw: list[Anomaly]) -> int:
    """Encode anomaly selections into a bitmask integer."""
    result = 0
    for anomaly in raw:
        result |= ANOMALY_BITS[anomaly]
    return result


def compute_confidence(
    overall: int,
    color: int,
    structure: int,
    growth: int,
    water: int,
) -> float:
    """Compute a consistency confidence score (0.0–1.0).

    Measures how well the overall score aligns with the sub-scores.
    A large deviation between overall and sub-score average lowers confidence.
    """
    sub_avg = (color + structure + growth + water) / 4.0
    deviation = abs(overall - sub_avg)
    # Max possible deviation is 4 (overall=5, sub_avg=1 or vice versa)
    confidence = max(0.0, 1.0 - (deviation / 4.0))
    return round(confidence, 3)


# ── Service Function ─────────────────────────────────────────────────


def create_evaluation(
    db: Session,
    session_token: str,
    data: PlantEvaluationCreate,
    ip: str,
    user_agent: str,
) -> PlantEvaluationResponse:
    """Validate session, map scores, compute confidence, and persist evaluation."""
    session = _get_valid_session(db, session_token)
    plant = db.query(Plant).filter(Plant.id == session.plant_id).first()
    if not plant:
        raise HTTPException(status_code=404, detail="Plant not found")

    # Resolve active sensor assignment
    active_assignment = (
        db.query(PlantSensorAssignment)
        .filter(
            PlantSensorAssignment.plant_id == plant.id,
            PlantSensorAssignment.is_active,
        )
        .first()
    )
    sensor_id = active_assignment.sensor_id if active_assignment else None

    # Map raw choices to numerical scores
    color_score = map_color_score(data.color_raw)
    structure_score = map_structure_score(data.structure_raw)
    growth_score = map_growth_score(data.growth_raw)
    water_score = map_water_score(data.water_raw)
    anomalies_vector = encode_anomalies(data.anomalies_raw)
    confidence = compute_confidence(
        data.overall_score, color_score, structure_score, growth_score, water_score
    )

    evaluation = PlantEvaluation(
        plant_id=plant.id,
        sensor_id=sensor_id,
        zone_id=plant.zone_id,
        session_id=session.id,
        overall_score=data.overall_score,
        color_score=color_score,
        structure_score=structure_score,
        growth_score=growth_score,
        water_score=water_score,
        anomalies_vector=anomalies_vector,
        color_raw=data.color_raw.value,
        structure_raw=data.structure_raw.value,
        growth_raw=data.growth_raw.value,
        water_raw=data.water_raw.value,
        anomalies_raw=",".join(a.value for a in data.anomalies_raw),
        confidence_score=confidence,
        detail_notes=data.detail_notes,
        used_ip=ip,
        user_agent=user_agent,
    )
    db.add(evaluation)
    db.commit()
    db.refresh(evaluation)

    logger.info(
        "Plant evaluation created",
        extra={
            "plant_id": str(plant.id),
            "overall_score": data.overall_score,
            "confidence": confidence,
        },
    )

    return PlantEvaluationResponse(
        id=str(evaluation.id),
        plant_id=str(evaluation.plant_id),
        sensor_id=str(evaluation.sensor_id) if evaluation.sensor_id else None,
        zone_id=str(evaluation.zone_id),
        evaluated_at=evaluation.evaluated_at.isoformat(),
        overall_score=evaluation.overall_score,
        color_score=evaluation.color_score,
        structure_score=evaluation.structure_score,
        growth_score=evaluation.growth_score,
        water_score=evaluation.water_score,
        anomalies_vector=evaluation.anomalies_vector,
        color_raw=evaluation.color_raw,
        structure_raw=evaluation.structure_raw,
        growth_raw=evaluation.growth_raw,
        water_raw=evaluation.water_raw,
        anomalies_raw=evaluation.anomalies_raw,
        confidence_score=evaluation.confidence_score,
        created_at=evaluation.created_at.isoformat(),
    )
