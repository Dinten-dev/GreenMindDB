"""Organization request/response schemas."""

from pydantic import BaseModel, ConfigDict


class OrgCreate(BaseModel):
    name: str


class OrgResponse(BaseModel):
    id: str
    name: str
    created_at: str

    model_config = ConfigDict(from_attributes=True)
