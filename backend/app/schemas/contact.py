"""Contact and Early Access form schemas."""

from pydantic import BaseModel, EmailStr, Field


class ContactRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    email: EmailStr
    company: str = Field(default="", max_length=200)
    message: str = Field(..., min_length=1, max_length=5000)
    # Honeypot: must be empty
    website: str | None = Field(default="")


class EarlyAccessRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    company: str = Field(..., min_length=1, max_length=200)
    email: EmailStr
    country: str = Field(..., min_length=1, max_length=100)
    message: str = Field(default="", max_length=5000)
    # Honeypot: must be empty
    website: str = Field(default="", max_length=0)
