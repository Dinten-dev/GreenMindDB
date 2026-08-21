"""Authentication request/response schemas."""

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class SignupRequest(BaseModel):
    email: EmailStr = Field(max_length=255)
    password: str = Field(max_length=128)
    name: str = Field("", max_length=200)

    @field_validator("password")
    @classmethod
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if not any(c.isupper() for c in v):
            raise ValueError("Password must include an uppercase letter")
        if not any(c.islower() for c in v):
            raise ValueError("Password must include a lowercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must include a number")
        return v


class LoginRequest(BaseModel):
    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=1, max_length=128)


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")


class ResendVerificationRequest(BaseModel):
    email: EmailStr = Field(max_length=255)


class UserResponse(BaseModel):
    id: str
    email: str
    name: str | None = None
    phone_number: str | None = None
    role: str
    organization_id: str | None = None
    organization_name: str | None = None
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class UserUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    phone_number: str | None = Field(None, max_length=50)


class AuthResponse(BaseModel):
    detail: str = "ok"
    user: UserResponse
