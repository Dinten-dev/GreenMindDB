"""Platform administration payloads. Existing login/password rules are reused."""

import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.user import Role
from app.schemas.auth import SignupRequest
from app.schemas.zone import ZoneCreate


class AdminUserCreate(SignupRequest):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=12, max_length=128)
    organization_id: uuid.UUID
    role: Role = Role.MEMBER
    zone_ids: list[uuid.UUID] = Field(default_factory=list, max_length=1000)


class AdminUserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    phone_number: str | None = Field(None, max_length=50)
    organization_id: uuid.UUID
    role: Role
    is_active: bool
    zone_ids: list[uuid.UUID] = Field(max_length=1000)


class AdminUserDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation_email: EmailStr


class AdminCompanyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)


class AdminCompanyDelete(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    confirmation_name: str = Field(min_length=1, max_length=200)


class AdminCompanyZoneCreate(ZoneCreate):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
