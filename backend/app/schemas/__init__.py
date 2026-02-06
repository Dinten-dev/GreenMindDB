"""Pydantic schemas for API request/response validation."""
from pydantic import BaseModel, field_validator
from typing import Optional


# === Metric Schemas ===
class MetricBase(BaseModel):
    key: str
    label: str
    unit: str
    description: Optional[str] = None


class MetricResponse(MetricBase):
    id: int
    
    class Config:
        from_attributes = True


# === Source Schemas ===
class SourceBase(BaseModel):
    title: str
    publisher: str
    year: Optional[int] = None
    url: Optional[str] = None
    notes: Optional[str] = None


class SourceCreate(SourceBase):
    """Schema for creating a new source."""
    pass


class SourceResponse(SourceBase):
    id: int
    
    class Config:
        from_attributes = True


# === Target Range Schemas ===
class TargetRangeBase(BaseModel):
    species_id: int
    metric_id: int
    optimal_low: float
    optimal_high: float
    source_id: int
    
    @field_validator('optimal_high')
    @classmethod
    def validate_optimal_range(cls, v, info):
        if 'optimal_low' in info.data and v < info.data['optimal_low']:
            raise ValueError('optimal_high must be >= optimal_low')
        return v


class TargetRangeCreate(TargetRangeBase):
    """Schema for creating a new target range."""
    pass


class TargetRangeUpdate(BaseModel):
    """Schema for updating a target range."""
    optimal_low: Optional[float] = None
    optimal_high: Optional[float] = None
    source_id: Optional[int] = None
    
    @field_validator('optimal_high')
    @classmethod
    def validate_optimal_range(cls, v, info):
        if v is not None and 'optimal_low' in info.data and info.data['optimal_low'] is not None:
            if v < info.data['optimal_low']:
                raise ValueError('optimal_high must be >= optimal_low')
        return v


class TargetRangeResponse(TargetRangeBase):
    id: int
    metric: MetricResponse
    source: SourceResponse
    
    class Config:
        from_attributes = True


# === Species Schemas ===
class SpeciesBase(BaseModel):
    common_name: str
    latin_name: str
    category: str
    notes: Optional[str] = None


class SpeciesCreate(BaseModel):
    """Schema for creating a new species."""
    common_name: str
    latin_name: Optional[str] = ""
    category: Optional[str] = "Unknown"
    notes: Optional[str] = None


class SpeciesUpdate(BaseModel):
    """Schema for updating a species."""
    common_name: Optional[str] = None
    latin_name: Optional[str] = None
    category: Optional[str] = None
    notes: Optional[str] = None


class SpeciesResponse(SpeciesBase):
    id: int
    
    class Config:
        from_attributes = True


class SpeciesDetailResponse(SpeciesResponse):
    """Species with all target ranges."""
    target_ranges: list[TargetRangeResponse] = []
    
    class Config:
        from_attributes = True
