"""Pydantic schemas for the GreenMind API.

All request/response schemas are centralized here, separated from
ORM models and router logic for cleaner architecture.
"""

from app.schemas.auth import (
    AuthResponse,
    LoginRequest,
    SignupRequest,
    UserResponse,
)
from app.schemas.contact import ContactRequest, EarlyAccessRequest
from app.schemas.evaluation import (
    PlantEvaluationCreate,
    PlantEvaluationResponse,
)
from app.schemas.gateway import (
    GatewayResponse,
    HeartbeatRequest,
    PairingCodeRequest,
    PairingCodeResponse,
    RegisterGatewayRequest,
    RegisterGatewayResponse,
)
from app.schemas.ingest import IngestRequest, IngestResponse, ReadingPayload
from app.schemas.observation import (
    ObservationAccessResponse,
    ObservationSessionCreate,
    ObservationSessionResponse,
    PlantObservationCreate,
    PlantObservationPhotoResponse,
    PlantObservationResponse,
    PublicPlantContextResponse,
)
from app.schemas.organization import OrgCreate, OrgResponse
from app.schemas.plant import (
    AssignSensorRequest,
    PlantCreate,
    PlantResponse,
    PlantSensorAssignmentResponse,
    PlantUpdate,
)
from app.schemas.sensor import (
    ClaimSensorRequest,
    ClaimSensorResponse,
    DataPoint,
    MoveSensorRequest,
    SensorDataResponse,
    SensorResponse,
)
from app.schemas.zone import (
    ZoneCreate,
    ZoneOverview,
    ZoneResponse,
)

__all__ = [
    "SignupRequest",
    "LoginRequest",
    "UserResponse",
    "AuthResponse",
    "OrgCreate",
    "OrgResponse",
    "ZoneCreate",
    "ZoneResponse",
    "ZoneOverview",
    "GatewayResponse",
    "PairingCodeRequest",
    "PairingCodeResponse",
    "RegisterGatewayRequest",
    "RegisterGatewayResponse",
    "HeartbeatRequest",
    "SensorResponse",
    "ClaimSensorRequest",
    "ClaimSensorResponse",
    "MoveSensorRequest",
    "DataPoint",
    "SensorDataResponse",
    "ReadingPayload",
    "IngestRequest",
    "IngestResponse",
    "ContactRequest",
    "EarlyAccessRequest",
    "PlantCreate",
    "PlantUpdate",
    "PlantResponse",
    "PlantSensorAssignmentResponse",
    "AssignSensorRequest",
    "ObservationAccessResponse",
    "ObservationSessionCreate",
    "ObservationSessionResponse",
    "PublicPlantContextResponse",
    "PlantObservationCreate",
    "PlantObservationResponse",
    "PlantObservationPhotoResponse",
    "PlantEvaluationCreate",
    "PlantEvaluationResponse",
]
