from datetime import datetime

from pydantic import UUID4, BaseModel, ConfigDict, Field, field_validator

from app.models.provisioning import ProvisioningStatus
from app.validation import normalize_mac_address


class ProvisioningJobCreate(BaseModel):
    ssid: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=0, max_length=128)
    pairing_code: str = Field(min_length=6, max_length=8, pattern=r"^[A-Za-z0-9]+$")

    @field_validator("pairing_code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return value.upper()


class ProvisioningJobUpdate(BaseModel):
    status: ProvisioningStatus
    mac_address: str | None = Field(None, min_length=12, max_length=17)

    @field_validator("mac_address")
    @classmethod
    def validate_mac(cls, value: str | None) -> str | None:
        return normalize_mac_address(value) if value is not None else None


class ProvisioningJobResponse(BaseModel):
    id: UUID4
    mac_address: str | None
    ssid: str
    password: str
    pairing_code: str
    status: ProvisioningStatus
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
