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
    signal_profile: Literal["ad8232-raw400-v1"] | None = None

    @model_validator(mode="after")
    def validate_shape(self):
        if self.signal_profile == "ad8232-raw400-v1" and (
            self.sample_rate != 400
            or self.channels != 1
            or self.sample_bits != 16
            or self.calibration_version != "unsigned-mv-linear-int16-v1"
        ):
            raise ValueError("Raw 400 Hz profile requires mono nominal GreenMind PCM16 at 400 Hz")
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

    def _metadata_dict(self, exclude=()):
        fields = set(exclude)
        # Preserve existing session configurations and duplicate identity hashes.
        # Do not use exclude_none globally: V2 timing intentionally retains null uncertainty.
        if self.signal_profile is None:
            fields.add("signal_profile")
        return self.model_dump(mode="json", exclude=fields)

    def session_config(self):
        return self._metadata_dict({"sequence", "first_frame", "frame_count", "payload_sha256"})

    def digest(self):
        return hashlib.sha256(
            json.dumps(self._metadata_dict(), sort_keys=True, separators=(",", ":")).encode()
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


class TimingV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal["ntp"]
    sync_epoch_us: int = Field(ge=1_700_000_000_000_000, le=4_102_444_800_000_000, strict=True)
    time_reference: Literal["nominal_sample_clock"]
    uncertainty_us: int | None = Field(ge=0, strict=True)


class ChunkMetadataV2(ChunkMetadata):
    protocol_version: Literal[2]
    boot_counter: int = Field(ge=1, le=2**63 - 1, strict=True)
    counter_generation: UUID
    timing: TimingV2

    @model_validator(mode="after")
    def validate_sync_time(self):
        if self.timing.sync_epoch_us > self.session_start_us:
            raise ValueError("NTP synchronization must precede session start")
        return self


def parse_metadata(value):
    if not isinstance(value, dict):
        raise ValueError("Metadata must be an object")
    version = value.get("protocol_version")
    if type(version) is not int or version not in (1, 2):
        raise ValueError("Unsupported Direct protocol")
    return (ChunkMetadata if version == 1 else ChunkMetadataV2).model_validate(value)
