"""Species API endpoints with full CRUD and audit logging."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload
from typing import Any, List, Optional
from datetime import datetime

from app.database import get_db
from app.models import Species, TargetRange, AuditLog, User
from app.schemas import (
    SpeciesResponse, SpeciesDetailResponse, TargetRangeResponse,
    SpeciesCreate, SpeciesUpdate
)
from app.auth import get_current_user
from app.audit import log_change, entity_to_dict
from pydantic import BaseModel

router = APIRouter(prefix="/species", tags=["species"])


def normalize_text(value: Optional[str]) -> Optional[str]:
    """Trim incoming string data and return None for None values."""
    if value is None:
        return None
    return value.strip()


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
    common_name = normalize_text(data.common_name) or ""
    latin_name = normalize_text(data.latin_name or "") or ""
    category = normalize_text(data.category or "Unknown") or "Unknown"
    notes = normalize_text(data.notes)

    if not common_name:
        raise HTTPException(status_code=400, detail="common_name must not be empty")

    existing = db.query(Species).filter(func.lower(Species.common_name) == common_name.lower()).first()
    if existing:
        raise HTTPException(status_code=409, detail="Species with this name already exists")

    species = Species(
        common_name=common_name,
        latin_name=latin_name,
        category=category,
        notes=notes
    )

    try:
        db.add(species)
        db.flush()
        log_change(
            db, current_user, "species", species.id, species.id,
            "CREATE", None, entity_to_dict(species)
        )
        db.commit()
        db.refresh(species)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Species with this name already exists")
    except Exception:
        db.rollback()
        raise

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

    common_name = normalize_text(data.common_name)
    latin_name = normalize_text(data.latin_name)
    category = normalize_text(data.category)
    notes = normalize_text(data.notes)

    if common_name is not None:
        if not common_name:
            raise HTTPException(status_code=400, detail="common_name must not be empty")
        if common_name.lower() != species.common_name.lower():
            existing = db.query(Species).filter(func.lower(Species.common_name) == common_name.lower()).first()
            if existing and existing.id != species_id:
                raise HTTPException(status_code=409, detail="Species with this name already exists")

    if data.common_name is not None:
        species.common_name = common_name or species.common_name
    if data.latin_name is not None:
        species.latin_name = latin_name or ""
    if data.category is not None:
        species.category = category or "Unknown"
    if data.notes is not None:
        species.notes = notes

    try:
        db.flush()
        log_change(
            db, current_user, "species", species.id, species.id,
            "UPDATE", before, entity_to_dict(species)
        )
        db.commit()
        db.refresh(species)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Species with this name already exists")
    except Exception:
        db.rollback()
        raise

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

    try:
        # Cascade delete: telemetry measurements -> channels -> target_ranges -> species
        db.execute(text("""
            DELETE FROM telemetry_measurement
            WHERE channel_id IN (SELECT id FROM telemetry_channel WHERE species_id = :sid)
        """), {"sid": species_id})

        db.execute(text("DELETE FROM telemetry_channel WHERE species_id = :sid"), {"sid": species_id})
        db.query(TargetRange).filter(TargetRange.species_id == species_id).delete()
        db.delete(species)
        log_change(
            db, current_user, "species", species_id, species_id,
            "DELETE", before, None
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

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
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db)
):
    """Get audit history for a species (public)."""
    species = db.query(Species).filter(Species.id == species_id).first()
    if not species:
        raise HTTPException(status_code=404, detail="Species not found")
    
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
