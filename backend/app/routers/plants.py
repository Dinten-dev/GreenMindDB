"""Router for Plant management within an organization."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.observation import ObservationAccessResponse
from app.schemas.plant import (
    AssignSensorRequest,
    PlantCreate,
    PlantResponse,
    PlantSensorAssignmentResponse,
    PlantUpdate,
)
from app.services.plant_service import (
    assign_sensor,
    create_plant,
    get_or_create_observation_access,
    get_plant,
    get_sensor_history,
    list_plants,
    revoke_observation_access,
    update_plant,
)

router = APIRouter(prefix="/plants", tags=["plants"])


@router.get("", response_model=list[PlantResponse])
def handle_list_plants(
    zone_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return list_plants(db, current_user, zone_id=zone_id)


@router.post("", response_model=PlantResponse, status_code=201)
def handle_create_plant(
    data: PlantCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return create_plant(db, current_user, data)


@router.get("/{plant_id}", response_model=PlantResponse)
def handle_get_plant(
    plant_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_plant(db, current_user, plant_id)


@router.patch("/{plant_id}", response_model=PlantResponse)
def handle_update_plant(
    plant_id: str,
    data: PlantUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return update_plant(db, current_user, plant_id, data)


@router.post("/{plant_id}/assign-sensor", response_model=PlantSensorAssignmentResponse)
def handle_assign_sensor(
    plant_id: str,
    data: AssignSensorRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return assign_sensor(db, current_user, plant_id, data)


@router.get("/{plant_id}/sensor-history", response_model=list[PlantSensorAssignmentResponse])
def handle_get_sensor_history(
    plant_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_sensor_history(db, current_user, plant_id)


@router.post("/{plant_id}/observation-access", response_model=ObservationAccessResponse)
def handle_create_observation_access(
    plant_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_or_create_observation_access(db, current_user, plant_id)


@router.delete("/{plant_id}/observation-access", response_model=ObservationAccessResponse)
def handle_revoke_observation_access(
    plant_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return revoke_observation_access(db, current_user, plant_id)
