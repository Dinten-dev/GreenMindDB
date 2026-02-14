"""Target Range API endpoints with inline source creation."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional
from urllib.parse import urlparse

from app.database import get_db
from app.models import TargetRange, Species, Metric, Source, User
from app.schemas import TargetRangeResponse
from app.auth import get_current_user
from app.audit import log_change, entity_to_dict
from pydantic import BaseModel, field_validator


router = APIRouter(prefix="/target-ranges", tags=["target-ranges"])


class SourceInput(BaseModel):
    """Inline source input - either URL or own experience."""
    source_type: str  # 'url' or 'own_experience'
    url: Optional[str] = None
    title: Optional[str] = None
    publisher: Optional[str] = None
    year: Optional[int] = None
    notes: Optional[str] = None

    @field_validator("source_type")
    @classmethod
    def validate_source_type(cls, value: str) -> str:
        source_type = value.strip().lower()
        if source_type not in {"url", "own_experience"}:
            raise ValueError("Invalid source type")
        return source_type

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        cleaned = value.strip()
        if not cleaned:
            return None
        parsed = urlparse(cleaned)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("url must be a valid http(s) URL")
        return cleaned

    @field_validator("year")
    @classmethod
    def validate_year(cls, value: Optional[int]) -> Optional[int]:
        if value is None:
            return value
        if value < 1900 or value > 2100:
            raise ValueError("year must be between 1900 and 2100")
        return value


class TargetRangeCreateWithSource(BaseModel):
    """Create target range with inline source."""
    species_id: int
    metric_id: int
    optimal_low: float
    optimal_high: float
    source: SourceInput


class TargetRangeUpdateWithSource(BaseModel):
    """Update target range with optional source."""
    optimal_low: Optional[float] = None
    optimal_high: Optional[float] = None
    source: Optional[SourceInput] = None


def clean_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def get_or_create_source(db: Session, source_input: SourceInput) -> Source:
    """Get existing source by URL or create new one."""
    if source_input.source_type == 'url':
        url = clean_text(source_input.url)
        if not url:
            raise HTTPException(status_code=400, detail="URL required for URL source")
        
        # Try to find existing source with same URL (dedup)
        existing = db.query(Source).filter(Source.url == url).first()
        if existing:
            return existing
        
        # Create new source
        source = Source(
            title=clean_text(source_input.title) or "External Source",
            publisher=clean_text(source_input.publisher) or "Unknown",
            year=source_input.year,
            url=url,
            notes=clean_text(source_input.notes)
        )
        db.add(source)
        db.flush()  # Get ID without committing
        return source
    
    elif source_input.source_type == 'own_experience':
        # Find or create the "Own experience" source
        own_exp = db.query(Source).filter(
            Source.publisher == "User",
            Source.title == "Own Experience"
        ).first()
        
        if not own_exp:
            own_exp = Source(
                title="Own Experience",
                publisher="User",
                notes=clean_text(source_input.notes) or "User observation"
            )
            db.add(own_exp)
            db.flush()
        
        return own_exp
    
    else:
        raise HTTPException(status_code=400, detail="Invalid source type")


@router.get("", response_model=List[TargetRangeResponse])
def list_target_ranges(db: Session = Depends(get_db)):
    """List all target ranges (public)."""
    return db.query(TargetRange).options(
        joinedload(TargetRange.metric),
        joinedload(TargetRange.source)
    ).all()


@router.post("", response_model=TargetRangeResponse, status_code=201)
def create_target_range(
    data: TargetRangeCreateWithSource,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Create a new target range with inline source (requires auth)."""
    # Validate species
    species = db.query(Species).filter(Species.id == data.species_id).first()
    if not species:
        raise HTTPException(status_code=404, detail="Species not found")
    
    # Validate metric
    metric = db.query(Metric).filter(Metric.id == data.metric_id).first()
    if not metric:
        raise HTTPException(status_code=404, detail="Metric not found")
    
    # Check unique constraint
    existing = db.query(TargetRange).filter(
        TargetRange.species_id == data.species_id,
        TargetRange.metric_id == data.metric_id
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Condition already exists for this metric")
    
    # Validate range
    if data.optimal_high < data.optimal_low:
        raise HTTPException(status_code=400, detail="optimal_high must be >= optimal_low")
    
    # Get or create source
    source = get_or_create_source(db, data.source)
    
    target_range = TargetRange(
        species_id=data.species_id,
        metric_id=data.metric_id,
        optimal_low=data.optimal_low,
        optimal_high=data.optimal_high,
        source_id=source.id
    )
    try:
        db.add(target_range)
        db.flush()
        log_change(
            db, current_user, "target_range", target_range.id, data.species_id,
            "CREATE", None, entity_to_dict(target_range)
        )
        db.commit()
        db.refresh(target_range)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Condition already exists for this metric")
    except Exception:
        db.rollback()
        raise
    
    # Reload with relationships
    return db.query(TargetRange).options(
        joinedload(TargetRange.metric),
        joinedload(TargetRange.source)
    ).filter(TargetRange.id == target_range.id).first()


@router.get("/{target_id}", response_model=TargetRangeResponse)
def get_target_range(target_id: int, db: Session = Depends(get_db)):
    """Get a single target range (public)."""
    target = db.query(TargetRange).options(
        joinedload(TargetRange.metric),
        joinedload(TargetRange.source)
    ).filter(TargetRange.id == target_id).first()
    
    if not target:
        raise HTTPException(status_code=404, detail="Target range not found")
    
    return target


@router.put("/{target_id}", response_model=TargetRangeResponse)
def update_target_range(
    target_id: int, 
    data: TargetRangeUpdateWithSource, 
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Update a target range (requires auth)."""
    target = db.query(TargetRange).filter(TargetRange.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target range not found")
    
    before = entity_to_dict(target)
    
    if data.optimal_low is not None:
        target.optimal_low = data.optimal_low
    if data.optimal_high is not None:
        target.optimal_high = data.optimal_high
    
    # Update source if provided
    if data.source:
        source = get_or_create_source(db, data.source)
        target.source_id = source.id
    
    # Validate final range
    if target.optimal_high < target.optimal_low:
        raise HTTPException(status_code=400, detail="optimal_high must be >= optimal_low")
    
    try:
        db.flush()
        log_change(
            db, current_user, "target_range", target_id, target.species_id,
            "UPDATE", before, entity_to_dict(target)
        )
        db.commit()
        db.refresh(target)
    except Exception:
        db.rollback()
        raise
    
    return db.query(TargetRange).options(
        joinedload(TargetRange.metric),
        joinedload(TargetRange.source)
    ).filter(TargetRange.id == target_id).first()


@router.delete("/{target_id}", status_code=204)
def delete_target_range(
    target_id: int, 
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Delete a target range (requires auth)."""
    target = db.query(TargetRange).filter(TargetRange.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target range not found")
    
    before = entity_to_dict(target)
    species_id = target.species_id

    try:
        db.delete(target)
        log_change(
            db, current_user, "target_range", target_id, species_id,
            "DELETE", before, None
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    
    return None
