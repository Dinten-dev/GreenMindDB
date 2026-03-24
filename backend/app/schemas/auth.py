"""Authentication request/response schemas."""

from pydantic import BaseModel, EmailStr, field_validator


class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    name: str = ""

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
    email: EmailStr
    password: str


class VerifyEmailRequest(BaseModel):
    token: str


class UserResponse(BaseModel):
    id: str
    email: str
    name: str | None = None
    role: str
    organization_id: str | None = None
    organization_name: str | None = None
    is_active: bool

    class Config:
        from_attributes = True


class AuthResponse(BaseModel):
    detail: str = "ok"
    user: UserResponse
