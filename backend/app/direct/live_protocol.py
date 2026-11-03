"""Bounded, diagnostic-only preview contract; no scientific raw payloads."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

RAW_PROFILE = "raw400-box32-v1"
AVERAGED_PROFILE = "avg20ms380-rational-v1"


class CaptureSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    session_id: UUID
    session_start_us: int = Field(ge=1_700_000_000_000_000, le=4_102_444_800_000_000, strict=True)
    source_rate: Literal[400, 380]
    raw_rate: Literal[400, 2000]
    preview_profile: Literal["raw400-box32-v1", "avg20ms380-rational-v1"]
    firmware_version: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9_.-]+$")

    @model_validator(mode="after")
    def coherent(self):
        expected = (400, 400) if self.preview_profile == RAW_PROFILE else (380, 2000)
        if (self.source_rate, self.raw_rate) != expected:
            raise ValueError("Inconsistent preview profile")
        return self


class PreviewPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    frame: int = Field(ge=0, le=2**48, strict=True)
    samples: int = Field(ge=30, le=32, strict=True)
    mv: float = Field(ge=0, le=3300, strict=True)


class RailWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    start_us: int = Field(ge=1_700_000_000_000_000, strict=True)
    end_us: int = Field(ge=1_700_000_000_000_000, strict=True)
    raw_samples: int = Field(ge=0, le=20000, strict=True)
    rail_samples: int = Field(ge=0, le=20000, strict=True)

    @model_validator(mode="after")
    def coherent(self):
        if not 0 < self.end_us - self.start_us <= 10_000_000:
            raise ValueError("Rail window must contain at most ten seconds")
        if self.rail_samples > self.raw_samples:
            raise ValueError("Rail numerator exceeds actual raw samples")
        return self


class PreviewPacket(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    generation: UUID
    session_id: UUID
    sequence: int = Field(ge=0, le=2**32 - 1, strict=True)
    points: list[PreviewPoint] = Field(min_length=1, max_length=8)
    rail: RailWindow


class MarkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    generation: UUID
    session_id: UUID
    frame: int = Field(ge=0, le=2**48, strict=True)
