"""Source API endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from urllib.parse import urlparse

from app.database import get_db
from app.models import Source, User
from app.schemas import SourceResponse, SourceCreate
from app.auth import get_current_user

router = APIRouter(prefix="/sources", tags=["sources"])


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


@router.get("", response_model=List[SourceResponse])
def list_sources(db: Session = Depends(get_db)):
    """List all data sources."""
    sources = db.query(Source).order_by(Source.publisher, Source.year.desc()).all()
    return sources


@router.post("", response_model=SourceResponse, status_code=201)
def create_source(
    data: SourceCreate,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user)
):
    """Create a new data source (requires auth)."""
    title = clean_text(data.title)
    publisher = clean_text(data.publisher)
    url = clean_text(data.url)
    notes = clean_text(data.notes)

    if not title:
        raise HTTPException(status_code=400, detail="title must not be empty")
    if not publisher:
        raise HTTPException(status_code=400, detail="publisher must not be empty")
    if url:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(status_code=400, detail="url must be a valid http(s) URL")

    source = Source(
        title=title,
        publisher=publisher,
        year=data.year,
        url=url,
        notes=notes
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
