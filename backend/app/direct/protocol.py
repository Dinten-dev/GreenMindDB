"""Version 1: interleaved signed PCM, frame index independent of arrival time."""

import hashlib
import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

SEGMENT_US = 600_000_000


class DirectError(Exception):
    def __init__(self, status: int, code: str):
        self.status = status
        self.code = code
        super().__init__(code)


class ChunkMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: Literal[1]
    device_id: UUID
    session_id: UUID
    sequence: int = Field(ge=0, le=2**63 - 1, strict=True)
    session_start_us: int = Field(ge=1_577_836_800_000_000, le=4_102_444_800_000_000, strict=True)
    first_frame: int = Field(ge=0, le=2**48, strict=True)
    sample_rate: int = Field(ge=1, le=2000, strict=True)
    channels: int = Field(ge=1, le=8, strict=True)
    sample_bits: Literal[16, 24]
    frame_count: int = Field(ge=1, le=20_000, strict=True)
    channel_labels: list[str] = Field(min_length=1, max_length=8)
    calibration_version: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9_.-]+$")
    firmware_version: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9_.-]+$")
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_shape(self):
        if self.calibration_version == "unsigned-mv-linear-int16-v1" and (
            self.sample_bits != 16 or self.channels != 1
        ):
            raise ValueError("Legacy calibration requires mono PCM16")
        if self.frame_count > 10 * self.sample_rate:
            raise ValueError("Chunks must not exceed ten seconds")
        if (
            len(self.channel_labels) != self.channels
            or len(set(self.channel_labels)) != self.channels
        ):
            raise ValueError("Channel labels must be unique and match channel count")
        if any(
            not label.isascii() or not label.isalnum() or len(label) > 16
            for label in self.channel_labels
        ):
            raise ValueError("Channel labels must contain 1..16 ASCII letters/digits")
        if self.first_frame + self.frame_count > 7_776_000 * self.sample_rate:
            raise ValueError("Start a new session at least every 90 days")
        return self

    @property
    def frame_bytes(self):
        return self.channels * (self.sample_bits // 8)

    def session_config(self):
        return self.model_dump(
            mode="json", exclude={"sequence", "first_frame", "frame_count", "payload_sha256"}
        )

    def digest(self):
        return hashlib.sha256(
            json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def pieces(self):
        """Yield UTC bucket, session frame bounds, payload offset and frame count."""
        cursor = self.first_frame
        end = cursor + self.frame_count
        while cursor < end:
            bucket = (self.session_start_us * self.sample_rate + cursor * 1_000_000) // (
                SEGMENT_US * self.sample_rate
            )
            lower = max(
                0,
                -(-((bucket * SEGMENT_US - self.session_start_us) * self.sample_rate) // 1_000_000),
            )
            upper = -(
                -(((bucket + 1) * SEGMENT_US - self.session_start_us) * self.sample_rate)
                // 1_000_000
            )
            stop = min(upper, end)
            yield bucket, lower, upper, cursor - self.first_frame, stop - cursor
            cursor = stop
