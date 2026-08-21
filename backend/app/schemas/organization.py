"""Organization request/response schemas."""

from pydantic import BaseModel, ConfigDict, Field


class OrgCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class OrgResponse(BaseModel):
    id: str
    name: str
    created_at: str

    model_config = ConfigDict(from_attributes=True)
