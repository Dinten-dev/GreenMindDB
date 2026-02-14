"""
Plant State API endpoints for ML-ready 1 Hz resampled data.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import text, select
from typing import List, Optional, Any
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone
import io
import csv

from app.database import get_db, SessionLocal
from app.auth import get_current_user
from app.models import Species
from app.models.plant_state import PlantState1Hz, ResamplingState
from app.services.resampling_service import run_pipeline_for_species, run_full_pipeline

router = APIRouter(
    prefix="/species/{species_id}/ml",
    tags=["ml-data"]
)

admin_router = APIRouter(
    prefix="/admin/resample",
    tags=["admin"]
)


# --- Pydantic Models ---

class PlantStateRow(BaseModel):
    timestamp: datetime
    air_temperature_c: Optional[float] = None
    rel_humidity_pct: Optional[float] = None
    light_ppfd: Optional[float] = None
    soil_moisture_pct: Optional[float] = None
    soil_ph: Optional[float] = None
    bio_voltage_mean: Optional[float] = None
    bio_voltage_std: Optional[float] = None
    quality_flags: Optional[dict] = None


class TimeseriesMLResponse(BaseModel):
    species_id: int
    count: int
    from_ts: Optional[datetime] = None
    to_ts: Optional[datetime] = None
    data: List[PlantStateRow]


class LatestMLResponse(BaseModel):
    species_id: int
    latest: Optional[PlantStateRow] = None


class ResampleTriggerResponse(BaseModel):
    status: str
    species_processed: int
    total_rows: int
    details: dict


# --- ML Data Endpoints ---

@router.get("/timeseries", response_model=TimeseriesMLResponse)
def get_ml_timeseries(
    species_id: int,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    limit: int = Query(default=3600, le=86400),  # Default 1 hour, max 1 day
    db: Session = Depends(get_db)
):
    """
    Get 1 Hz resampled timeseries data for ML training.
    
    Returns a dense time grid with all features aligned.
    """
    # Verify species exists
    species = db.query(Species).filter(Species.id == species_id).first()
    if not species:
        raise HTTPException(status_code=404, detail="Species not found")
    
    # Default time range
    if not to_date:
        to_date = datetime.now(timezone.utc)
    if not from_date:
        from_date = to_date - timedelta(seconds=limit)
    
    # Query plant_state_1hz
    query = text("""
        SELECT 
            timestamp,
            air_temperature_c,
            rel_humidity_pct,
            light_ppfd,
            soil_moisture_pct,
            soil_ph,
            bio_voltage_mean,
            bio_voltage_std,
            quality_flags
        FROM plant_state_1hz
        WHERE species_id = :species_id
          AND timestamp >= :from_date
          AND timestamp <= :to_date
        ORDER BY timestamp ASC
        LIMIT :limit
    """)
    
    rows = db.execute(query, {
        "species_id": species_id,
        "from_date": from_date,
        "to_date": to_date,
        "limit": limit
    }).fetchall()
    
    data = [
        PlantStateRow(
            timestamp=r.timestamp,
            air_temperature_c=r.air_temperature_c,
            rel_humidity_pct=r.rel_humidity_pct,
            light_ppfd=r.light_ppfd,
            soil_moisture_pct=r.soil_moisture_pct,
            soil_ph=r.soil_ph,
            bio_voltage_mean=r.bio_voltage_mean,
            bio_voltage_std=r.bio_voltage_std,
            quality_flags=r.quality_flags
        )
        for r in rows
    ]
    
    return TimeseriesMLResponse(
        species_id=species_id,
        count=len(data),
        from_ts=data[0].timestamp if data else None,
        to_ts=data[-1].timestamp if data else None,
        data=data
    )


@router.get("/latest", response_model=LatestMLResponse)
def get_ml_latest(
    species_id: int,
    db: Session = Depends(get_db)
):
    """Get the most recent 1 Hz resampled state for a species."""
    # Verify species exists
    species = db.query(Species).filter(Species.id == species_id).first()
    if not species:
        raise HTTPException(status_code=404, detail="Species not found")
    
    query = text("""
        SELECT 
            timestamp,
            air_temperature_c,
            rel_humidity_pct,
            light_ppfd,
            soil_moisture_pct,
            soil_ph,
            bio_voltage_mean,
            bio_voltage_std,
            quality_flags
        FROM plant_state_1hz
        WHERE species_id = :species_id
        ORDER BY timestamp DESC
        LIMIT 1
    """)
    
    row = db.execute(query, {"species_id": species_id}).fetchone()
    
    latest = None
    if row:
        latest = PlantStateRow(
            timestamp=row.timestamp,
            air_temperature_c=row.air_temperature_c,
            rel_humidity_pct=row.rel_humidity_pct,
            light_ppfd=row.light_ppfd,
            soil_moisture_pct=row.soil_moisture_pct,
            soil_ph=row.soil_ph,
            bio_voltage_mean=row.bio_voltage_mean,
            bio_voltage_std=row.bio_voltage_std,
            quality_flags=row.quality_flags
        )
    
    return LatestMLResponse(species_id=species_id, latest=latest)


@router.get("/download")
def download_ml_csv(
    species_id: int,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    db: Session = Depends(get_db)
):
    """
    Download 1 Hz resampled data as CSV for ML training.
    Direct import into Pandas/PyTorch.
    """
    if not to_date:
        to_date = datetime.now(timezone.utc)
    if not from_date:
        from_date = to_date - timedelta(hours=24)
    
    query = text("""
        SELECT 
            timestamp,
            air_temperature_c,
            rel_humidity_pct,
            light_ppfd,
            soil_moisture_pct,
            soil_ph,
            bio_voltage_mean,
            bio_voltage_std
        FROM plant_state_1hz
        WHERE species_id = :species_id
          AND timestamp >= :from_date
          AND timestamp <= :to_date
        ORDER BY timestamp ASC
    """)
    
    rows = db.execute(query, {
        "species_id": species_id,
        "from_date": from_date,
        "to_date": to_date
    }).fetchall()
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Header
    writer.writerow([
        "timestamp",
        "air_temperature_c",
        "rel_humidity_pct",
        "light_ppfd",
        "soil_moisture_pct",
        "soil_ph",
        "bio_voltage_mean",
        "bio_voltage_std"
    ])
    
    # Data rows
    for r in rows:
        writer.writerow([
            r.timestamp.isoformat(),
            r.air_temperature_c if r.air_temperature_c is not None else "",
            r.rel_humidity_pct if r.rel_humidity_pct is not None else "",
            r.light_ppfd if r.light_ppfd is not None else "",
            r.soil_moisture_pct if r.soil_moisture_pct is not None else "",
            r.soil_ph if r.soil_ph is not None else "",
            r.bio_voltage_mean if r.bio_voltage_mean is not None else "",
            r.bio_voltage_std if r.bio_voltage_std is not None else ""
        ])
    
    output.seek(0)
    
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=species_{species_id}_1hz.csv"
        }
    )


# --- Admin Endpoints ---

@admin_router.post("/trigger", response_model=ResampleTriggerResponse)
def trigger_resampling(
    species_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    """
    Manually trigger the resampling pipeline.
    
    If species_id is provided, only that species is processed.
    Otherwise, all species are processed.
    """
    if species_id:
        # Process single species
        try:
            count = run_pipeline_for_species(db, species_id)
            return ResampleTriggerResponse(
                status="success",
                species_processed=1,
                total_rows=count,
                details={species_id: count}
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    else:
        # Process all species
        try:
            results = run_full_pipeline(db)
            total = sum(v for v in results.values() if v > 0)
            return ResampleTriggerResponse(
                status="success",
                species_processed=len(results),
                total_rows=total,
                details=results
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
