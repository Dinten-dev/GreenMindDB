"""Species API endpoints with full CRUD and audit logging."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional

from app.database import get_db
from app.models import Species, TargetRange, AuditLog, User
from app.schemas import (
    SpeciesResponse, SpeciesDetailResponse, TargetRangeResponse,
    SpeciesCreate, SpeciesUpdate
)
from app.auth import get_current_user, get_current_user_optional
from app.audit import log_change, entity_to_dict

router = APIRouter(prefix="/species", tags=["species"])


@router.get("", response_model=List[SpeciesResponse])
def list_species(db: Session = Depends(get_db)):
    """List all plant species (public)."""
    return db.query(Species).order_by(Species.common_name).all()


@router.post("", response_model=SpeciesResponse, status_code=201)
def create_species(
    data: SpeciesCreate, 
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Create a new plant species (requires auth)."""
    existing = db.query(Species).filter(Species.common_name == data.common_name).first()
    if existing:
        raise HTTPException(status_code=409, detail="Species with this name already exists")
    
    species = Species(
        common_name=data.common_name,
        latin_name=data.latin_name or "",
        category=data.category or "Unknown",
        notes=data.notes
    )
    db.add(species)
    db.commit()
    db.refresh(species)
    
    # Audit log
    log_change(
        db, current_user, "species", species.id, species.id, 
        "CREATE", None, entity_to_dict(species)
    )
    
    return species


@router.get("/{species_id}", response_model=SpeciesDetailResponse)
def get_species(species_id: int, db: Session = Depends(get_db)):
    """Get a single species with all target ranges (public)."""
    species = db.query(Species).options(
        joinedload(Species.target_ranges).joinedload(TargetRange.metric),
        joinedload(Species.target_ranges).joinedload(TargetRange.source)
    ).filter(Species.id == species_id).first()
    
    if not species:
        raise HTTPException(status_code=404, detail="Species not found")
    
    return species


@router.put("/{species_id}", response_model=SpeciesResponse)
def update_species(
    species_id: int, 
    data: SpeciesUpdate, 
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Update a species (requires auth)."""
    species = db.query(Species).filter(Species.id == species_id).first()
    if not species:
        raise HTTPException(status_code=404, detail="Species not found")
    
    before = entity_to_dict(species)
    
    if data.common_name and data.common_name != species.common_name:
        existing = db.query(Species).filter(Species.common_name == data.common_name).first()
        if existing:
            raise HTTPException(status_code=409, detail="Species with this name already exists")
    
    if data.common_name is not None:
        species.common_name = data.common_name
    if data.latin_name is not None:
        species.latin_name = data.latin_name
    if data.category is not None:
        species.category = data.category
    if data.notes is not None:
        species.notes = data.notes
    
    db.commit()
    db.refresh(species)
    
    # Audit log
    log_change(
        db, current_user, "species", species.id, species.id,
        "UPDATE", before, entity_to_dict(species)
    )
    
    return species


@router.delete("/{species_id}", status_code=204)
def delete_species(
    species_id: int, 
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Delete a species and all associated data (requires auth)."""
    from sqlalchemy import text
    
    species = db.query(Species).filter(Species.id == species_id).first()
    if not species:
        raise HTTPException(status_code=404, detail="Species not found")
    
    before = entity_to_dict(species)
    
    # Cascade delete: telemetry measurements -> channels -> target_ranges -> species
    # First delete measurements for channels belonging to this species
    db.execute(text("""
        DELETE FROM telemetry_measurement 
        WHERE channel_id IN (SELECT id FROM telemetry_channel WHERE species_id = :sid)
    """), {"sid": species_id})
    
    # Delete telemetry channels
    db.execute(text("DELETE FROM telemetry_channel WHERE species_id = :sid"), {"sid": species_id})
    
    # Delete target ranges (conditions)
    db.query(TargetRange).filter(TargetRange.species_id == species_id).delete()
    
    # Delete the species
    db.delete(species)
    db.commit()
    
    # Audit log
    log_change(
        db, current_user, "species", species_id, species_id,
        "DELETE", before, None
    )
    
    return None


@router.get("/{species_id}/targets", response_model=List[TargetRangeResponse])
def get_species_targets(species_id: int, db: Session = Depends(get_db)):
    """Get target ranges for a species (public)."""
    species = db.query(Species).filter(Species.id == species_id).first()
    if not species:
        raise HTTPException(status_code=404, detail="Species not found")
    
    targets = db.query(TargetRange).options(
        joinedload(TargetRange.metric),
        joinedload(TargetRange.source)
    ).filter(TargetRange.species_id == species_id).all()
    
    return targets


# Audit history endpoint
class AuditLogResponse(SpeciesResponse):
    pass


from pydantic import BaseModel
from datetime import datetime
from typing import Optional, Any

class AuditEntryResponse(BaseModel):
    id: int
    timestamp: datetime
    user_email: Optional[str]
    entity_type: str
    action: str
    diff_json: Any
    
    class Config:
        from_attributes = True


@router.get("/{species_id}/history", response_model=List[AuditEntryResponse])
def get_species_history(
    species_id: int, 
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """Get audit history for a species (public)."""
    species = db.query(Species).filter(Species.id == species_id).first()
    if not species:
        raise HTTPException(status_code=404, detail="Species not found")
    
    from app.models import User
    
    logs = db.query(AuditLog).options(
        joinedload(AuditLog.user)
    ).filter(
        AuditLog.species_id == species_id
    ).order_by(AuditLog.timestamp.desc()).limit(limit).all()
    
    result = []
    for log in logs:
        result.append({
            "id": log.id,
            "timestamp": log.timestamp,
            "user_email": log.user.email if log.user else None,
            "entity_type": log.entity_type,
            "action": log.action,
            "diff_json": log.diff_json
        })
    
    return result
