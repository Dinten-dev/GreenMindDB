"""SQLAlchemy models for plant state 1Hz resampled data."""
from sqlalchemy import Column, Integer, Float, DateTime, ForeignKey, text
from sqlalchemy.dialects.postgresql import TIMESTAMP, JSONB
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base


class PlantState1Hz(Base):
    """
    ML-ready plant state at 1 Hz resolution.
    Each row represents the complete state of a plant at a specific second.
    """
    __tablename__ = "plant_state_1hz"

    # Composite primary key: (species_id, timestamp)
    timestamp = Column(TIMESTAMP(timezone=True), primary_key=True, nullable=False, index=True)
    species_id = Column(Integer, ForeignKey("species.id", ondelete="CASCADE"), primary_key=True, nullable=False, index=True)
    
    # Environment metrics
    air_temperature_c = Column(Float, nullable=True)
    rel_humidity_pct = Column(Float, nullable=True)
    light_ppfd = Column(Float, nullable=True)
    
    # Soil metrics
    soil_moisture_pct = Column(Float, nullable=True)
    soil_ph = Column(Float, nullable=True)
    
    # Bioelectric metrics (aggregated from high-freq WAV)
    bio_voltage_mean = Column(Float, nullable=True)
    bio_voltage_std = Column(Float, nullable=True)
    
    # Quality metadata
    quality_flags = Column(JSONB, server_default="{}", nullable=True)
    
    # Relationships
    species = relationship("Species")


class ResamplingState(Base):
    """
    Tracks the resampling pipeline progress per species.
    Used for idempotent, incremental processing.
    """
    __tablename__ = "resampling_state"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    species_id = Column(Integer, ForeignKey("species.id", ondelete="CASCADE"), nullable=False, unique=True)
    last_processed_ts = Column(TIMESTAMP(timezone=True), server_default=text("'1970-01-01T00:00:00Z'::timestamptz"), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=text("now()"), nullable=False)
    
    # Relationships
    species = relationship("Species")
