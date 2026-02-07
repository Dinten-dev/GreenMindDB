from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from uuid import UUID

# --- Ingest Schemas ---

class IngestSample(BaseModel):
    timestamp: datetime
    metric_key: str
    species_name: Optional[str] = None # Optional if device is bound to one species, but plan says resolve
    value: float

class IngestPayload(BaseModel):
    device_id: UUID
    samples: List[IngestSample]

# --- Live Data Schemas ---

class MetricValue(BaseModel):
    metric_key: str
    value: float
    unit: str
    timestamp: datetime

class LatestValuesResponse(BaseModel):
    species_id: int
    latest: List[MetricValue]

class TimeseriesPoint(BaseModel):
    timestamp: datetime
    value: float

class TimeseriesResponse(BaseModel):
    metric_key: str
    unit: str
    points: List[List[float]] # [timestamp_ms, value] or just list of objects? 
    # Plan said: points:[["2026-02-06T10:00:00Z",23.1], ...]
    # So List[List[Union[str, float]]] but pydantic generic list list is easier as List[tuple] or similar.
    # Let's use List[tuple[datetime, float]] and let FastAPI/Pydantic serialize it.
    points: List[tuple[datetime, float]] 
