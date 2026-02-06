"""Source API endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.models import Source
from app.schemas import SourceResponse, SourceCreate

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("", response_model=List[SourceResponse])
def list_sources(db: Session = Depends(get_db)):
    """List all data sources."""
    sources = db.query(Source).order_by(Source.publisher, Source.year.desc()).all()
    return sources


@router.post("", response_model=SourceResponse, status_code=201)
def create_source(data: SourceCreate, db: Session = Depends(get_db)):
    """Create a new data source."""
    source = Source(
        title=data.title,
        publisher=data.publisher,
        year=data.year,
        url=data.url,
        notes=data.notes
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


@router.get("/{source_id}", response_model=SourceResponse)
def get_source(source_id: int, db: Session = Depends(get_db)):
    """Get a single source."""
    source = db.query(Source).filter(Source.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    return source
