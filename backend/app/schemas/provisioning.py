from datetime import datetime

from pydantic import UUID4, BaseModel, constr

from app.models.provisioning import ProvisioningStatus


class ProvisioningJobCreate(BaseModel):
    ssid: str
    password: str
    pairing_code: constr(min_length=6, max_length=6)

class ProvisioningJobUpdate(BaseModel):
    status: ProvisioningStatus
    mac_address: str | None = None

class ProvisioningJobResponse(BaseModel):
    id: UUID4
    mac_address: str | None
    ssid: str
    password: str
    pairing_code: str
    status: ProvisioningStatus
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True
