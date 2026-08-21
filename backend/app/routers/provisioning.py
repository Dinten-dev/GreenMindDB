import asyncio
from datetime import UTC, datetime

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import UUID4
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.gateway_auth import authenticate_gateway_api_key, get_current_gateway
from app.models.master import Gateway, Zone
from app.models.pairing import PairingCode
from app.models.provisioning import ProvisioningJob, ProvisioningStatus
from app.models.user import User
from app.schemas.provisioning import (
    ProvisioningJobCreate,
    ProvisioningJobResponse,
    ProvisioningJobUpdate,
)

router = APIRouter(prefix="/provisioning", tags=["provisioning"])

# Very simple global set for the gateway to connect and receive new jobs
# In production with multiple workers, this would use Redis pub/sub.
connected_gateways: dict[WebSocket, str] = {}


@router.websocket("/ws")
async def provisioning_ws(
    websocket: WebSocket,
    db: Session = Depends(get_db),
    x_api_key: str | None = Header(None, alias="X-Api-Key"),
):
    """
    WebSocket endpoint for the Gateway.
    The Gateway connects here to receive real-time notifications about new provisioning jobs.
    """
    try:
        gateway = authenticate_gateway_api_key(db, x_api_key)
    except HTTPException:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await websocket.accept()
    connected_gateways[websocket] = str(gateway.id)
    try:
        while True:
            # Keep connection alive
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        connected_gateways.pop(websocket, None)


async def notify_gateways():
    """Notify all connected gateways that a new job is available."""
    for ws in list(connected_gateways):
        try:
            await ws.send_json({"event": "new_job_available"})
        except Exception:
            connected_gateways.pop(ws, None)


@router.post("/jobs", response_model=ProvisioningJobResponse, status_code=status.HTTP_201_CREATED)
async def create_provisioning_job(
    job_in: ProvisioningJobCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new provisioning job from the Dashboard."""
    pairing_code = (
        db.query(PairingCode)
        .join(Zone, Zone.id == PairingCode.zone_id)
        .filter(
            PairingCode.code == job_in.pairing_code,
            PairingCode.used_at.is_(None),
            PairingCode.expires_at > datetime.now(UTC),
            Zone.organization_id == current_user.organization_id,
        )
        .first()
    )
    if not pairing_code:
        raise HTTPException(status_code=400, detail="Invalid or expired pairing code")

    job = ProvisioningJob(
        ssid=job_in.ssid,
        password=job_in.password,
        pairing_code=job_in.pairing_code,
        status=ProvisioningStatus.PENDING,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # Notify connected gateways via WebSocket
    asyncio.create_task(notify_gateways())

    return job


@router.get("/jobs/pending", response_model=list[ProvisioningJobResponse])
def get_pending_jobs(
    gateway: Gateway = Depends(get_current_gateway),
    db: Session = Depends(get_db),
):
    """
    Gateway polls this endpoint (fallback) to get all pending jobs.
    """
    jobs = (
        db.query(ProvisioningJob)
        .join(PairingCode, PairingCode.code == ProvisioningJob.pairing_code)
        .filter(
            ProvisioningJob.status == ProvisioningStatus.PENDING,
            PairingCode.zone_id == gateway.zone_id,
        )
        .all()
    )
    return jobs


@router.patch("/jobs/{job_id}", response_model=ProvisioningJobResponse)
def update_provisioning_job(
    job_id: UUID4,
    job_in: ProvisioningJobUpdate,
    gateway: Gateway = Depends(get_current_gateway),
    db: Session = Depends(get_db),
):
    """Gateway calls this to update the job status after provisioning."""
    job = (
        db.query(ProvisioningJob)
        .join(PairingCode, PairingCode.code == ProvisioningJob.pairing_code)
        .filter(ProvisioningJob.id == job_id, PairingCode.zone_id == gateway.zone_id)
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    job.status = job_in.status
    if job_in.mac_address:
        job.mac_address = job_in.mac_address

    db.commit()
    db.refresh(job)
    return job
