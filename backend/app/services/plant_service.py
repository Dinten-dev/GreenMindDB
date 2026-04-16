"""Business logic for Plant management."""

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models.observation import PlantObservationAccess
from app.models.plant import Plant, PlantSensorAssignment
from app.models.user import User
from app.models.master import Zone
from app.schemas.observation import ObservationAccessResponse
from app.schemas.plant import (
    AssignSensorRequest,
    PlantCreate,
    PlantResponse,
    PlantSensorAssignmentResponse,
    PlantUpdate,
)


def _require_org(user: User):
    if not user.organization_id:
        raise HTTPException(status_code=400, detail="User must belong to an organization")
    return user.organization_id


def list_plants(db: Session, user: User, zone_id: str | None = None) -> list[PlantResponse]:
    org_id = _require_org(user)
    query = db.query(Plant).filter(Plant.organization_id == org_id)
    if zone_id:
        query = query.filter(Plant.zone_id == zone_id)
    
    plants = query.all()
    results = []
    for p in plants:
        # Find active sensor assignment if any
        active_assignment = (
            db.query(PlantSensorAssignment)
            .filter(
                PlantSensorAssignment.plant_id == p.id,
                PlantSensorAssignment.is_active == True
            )
            .first()
        )
        current_sensor_id = str(active_assignment.sensor_id) if active_assignment else None
        
        results.append(
            PlantResponse(
                id=str(p.id),
                organization_id=str(p.organization_id),
                zone_id=str(p.zone_id),
                name=p.name,
                plant_code=p.plant_code,
                species=p.species,
                cultivar=p.cultivar,
                description=p.description,
                planted_at=p.planted_at.isoformat() if p.planted_at else None,
                status=p.status,
                created_at=p.created_at.isoformat(),
                updated_at=p.updated_at.isoformat(),
                current_sensor_id=current_sensor_id,
            )
        )
    return results


def create_plant(db: Session, user: User, data: PlantCreate) -> PlantResponse:
    org_id = _require_org(user)
    
    # Verify zone
    zone = db.query(Zone).filter(Zone.id == data.zone_id, Zone.organization_id == org_id).first()
    if not zone:
        raise HTTPException(status_code=404, detail="Zone not found")
        
    p = Plant(
        organization_id=org_id,
        zone_id=data.zone_id,
        name=data.name,
        plant_code=data.plant_code,
        species=data.species,
        cultivar=data.cultivar,
        description=data.description,
        planted_at=data.planted_at,
        status=data.status,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    
    return PlantResponse(
        id=str(p.id),
        organization_id=str(p.organization_id),
        zone_id=str(p.zone_id),
        name=p.name,
        plant_code=p.plant_code,
        species=p.species,
        cultivar=p.cultivar,
        description=p.description,
        planted_at=p.planted_at.isoformat() if p.planted_at else None,
        status=p.status,
        created_at=p.created_at.isoformat(),
        updated_at=p.updated_at.isoformat(),
        current_sensor_id=None,
    )


def get_plant(db: Session, user: User, plant_id: str) -> PlantResponse:
    org_id = _require_org(user)
    p = db.query(Plant).filter(Plant.id == plant_id, Plant.organization_id == org_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Plant not found")
        
    active_assignment = (
        db.query(PlantSensorAssignment)
        .filter(
            PlantSensorAssignment.plant_id == p.id,
            PlantSensorAssignment.is_active == True
        )
        .first()
    )
    current_sensor_id = str(active_assignment.sensor_id) if active_assignment else None

    return PlantResponse(
        id=str(p.id),
        organization_id=str(p.organization_id),
        zone_id=str(p.zone_id),
        name=p.name,
        plant_code=p.plant_code,
        species=p.species,
        cultivar=p.cultivar,
        description=p.description,
        planted_at=p.planted_at.isoformat() if p.planted_at else None,
        status=p.status,
        created_at=p.created_at.isoformat(),
        updated_at=p.updated_at.isoformat(),
        current_sensor_id=current_sensor_id,
    )


def update_plant(db: Session, user: User, plant_id: str, data: PlantUpdate) -> PlantResponse:
    org_id = _require_org(user)
    p = db.query(Plant).filter(Plant.id == plant_id, Plant.organization_id == org_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Plant not found")
        
    if data.name is not None:
        p.name = data.name
    if data.plant_code is not None:
        p.plant_code = data.plant_code
    if data.species is not None:
        p.species = data.species
    if data.cultivar is not None:
        p.cultivar = data.cultivar
    if data.description is not None:
        p.description = data.description
    if data.status is not None:
        p.status = data.status
        
    p.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(p)
    return get_plant(db, user, str(p.id))


def assign_sensor(db: Session, user: User, plant_id: str, data: AssignSensorRequest) -> PlantSensorAssignmentResponse:
    org_id = _require_org(user)
    p = db.query(Plant).filter(Plant.id == plant_id, Plant.organization_id == org_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Plant not found")
        
    # Unassign current if any
    active_assignment = (
        db.query(PlantSensorAssignment)
        .filter(
            PlantSensorAssignment.plant_id == p.id,
            PlantSensorAssignment.is_active == True
        )
        .first()
    )
    if active_assignment:
        if str(active_assignment.sensor_id) == data.sensor_id:
            # Already assigned to this sensor
            return PlantSensorAssignmentResponse(
                id=str(active_assignment.id),
                plant_id=str(active_assignment.plant_id),
                sensor_id=str(active_assignment.sensor_id),
                assigned_at=active_assignment.assigned_at.isoformat(),
                unassigned_at=None,
                notes=active_assignment.notes,
                is_active=True,
            )
        active_assignment.is_active = False
        active_assignment.unassigned_at = datetime.now(UTC)
        
    # Create new assignment
    new_assignment = PlantSensorAssignment(
        plant_id=p.id,
        sensor_id=data.sensor_id,
        assigned_by_user_id=user.id,
        notes=data.notes,
        is_active=True,
    )
    db.add(new_assignment)
    db.commit()
    db.refresh(new_assignment)
    
    return PlantSensorAssignmentResponse(
        id=str(new_assignment.id),
        plant_id=str(new_assignment.plant_id),
        sensor_id=str(new_assignment.sensor_id),
        assigned_at=new_assignment.assigned_at.isoformat(),
        unassigned_at=None,
        notes=new_assignment.notes,
        is_active=True,
    )


def get_sensor_history(db: Session, user: User, plant_id: str) -> list[PlantSensorAssignmentResponse]:
    org_id = _require_org(user)
    p = db.query(Plant).filter(Plant.id == plant_id, Plant.organization_id == org_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Plant not found")
        
    assignments = (
        db.query(PlantSensorAssignment)
        .filter(PlantSensorAssignment.plant_id == p.id)
        .order_by(PlantSensorAssignment.assigned_at.desc())
        .all()
    )
    return [
        PlantSensorAssignmentResponse(
            id=str(a.id),
            plant_id=str(a.plant_id),
            sensor_id=str(a.sensor_id),
            assigned_at=a.assigned_at.isoformat(),
            unassigned_at=a.unassigned_at.isoformat() if a.unassigned_at else None,
            notes=a.notes,
            is_active=a.is_active,
        )
        for a in assignments
    ]


def get_or_create_observation_access(db: Session, user: User, plant_id: str) -> ObservationAccessResponse:
    org_id = _require_org(user)
    p = db.query(Plant).filter(Plant.id == plant_id, Plant.organization_id == org_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Plant not found")
        
    access = (
        db.query(PlantObservationAccess)
        .filter(
            PlantObservationAccess.plant_id == p.id,
            PlantObservationAccess.is_active == True
        )
        .first()
    )
    if not access:
        access = PlantObservationAccess(
            plant_id=p.id,
            public_id=uuid.uuid4(),
            created_by_user_id=user.id,
            is_active=True,
            usage_count=0
        )
        db.add(access)
        db.commit()
        db.refresh(access)
        
    return ObservationAccessResponse(
        id=str(access.id),
        plant_id=str(access.plant_id),
        public_id=str(access.public_id),
        is_active=access.is_active,
        usage_count=access.usage_count,
        revoked_at=access.revoked_at.isoformat() if access.revoked_at else None
    )


def revoke_observation_access(db: Session, user: User, plant_id: str) -> ObservationAccessResponse:
    org_id = _require_org(user)
    p = db.query(Plant).filter(Plant.id == plant_id, Plant.organization_id == org_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Plant not found")
        
    access = (
        db.query(PlantObservationAccess)
        .filter(
            PlantObservationAccess.plant_id == p.id,
            PlantObservationAccess.is_active == True
        )
        .first()
    )
    if not access:
        raise HTTPException(status_code=400, detail="No active observation access found")
        
    access.is_active = False
    access.revoked_at = datetime.now(UTC)
    db.commit()
    db.refresh(access)
    
    return ObservationAccessResponse(
        id=str(access.id),
        plant_id=str(access.plant_id),
        public_id=str(access.public_id),
        is_active=access.is_active,
        usage_count=access.usage_count,
        revoked_at=access.revoked_at.isoformat() if access.revoked_at else None
    )
