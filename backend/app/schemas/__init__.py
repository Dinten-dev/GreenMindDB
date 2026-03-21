"""Pydantic schemas for the GreenMind API.

All request/response schemas are centralized here, separated from
ORM models and router logic for cleaner architecture.
"""

from app.schemas.auth import (
    LoginRequest,
    SignupRequest,
    TokenResponse,
    UserResponse,
)
from app.schemas.contact import ContactRequest, EarlyAccessRequest
from app.schemas.device import (
    DeviceResponse,
    PairDeviceRequest,
    PairDeviceResponse,
    PairingCodeRequest,
    PairingCodeResponse,
)
from app.schemas.greenhouse import (
    GreenhouseCreate,
    GreenhouseOverview,
    GreenhouseResponse,
)
from app.schemas.ingest import IngestRequest, IngestResponse, ReadingPayload
from app.schemas.organization import OrgCreate, OrgResponse
from app.schemas.sensor import DataPoint, SensorDataResponse, SensorResponse

__all__ = [
    "SignupRequest",
    "LoginRequest",
    "UserResponse",
    "TokenResponse",
    "OrgCreate",
    "OrgResponse",
    "GreenhouseCreate",
    "GreenhouseResponse",
    "GreenhouseOverview",
    "DeviceResponse",
    "PairingCodeRequest",
    "PairingCodeResponse",
    "PairDeviceRequest",
    "PairDeviceResponse",
    "SensorResponse",
    "DataPoint",
    "SensorDataResponse",
    "ReadingPayload",
    "IngestRequest",
    "IngestResponse",
    "ContactRequest",
    "EarlyAccessRequest",
]
