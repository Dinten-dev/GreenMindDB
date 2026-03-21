"""Organization request/response schemas."""

from pydantic import BaseModel


class OrgCreate(BaseModel):
    name: str


class OrgResponse(BaseModel):
    id: str
    name: str
    created_at: str

    class Config:
        from_attributes = True
