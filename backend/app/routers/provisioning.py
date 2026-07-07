import asyncio

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import UUID4
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models.provisioning import ProvisioningJob, ProvisioningStatus
from app.schemas.provisioning import (
    ProvisioningJobCreate,
    ProvisioningJobResponse,
    ProvisioningJobUpdate,
)

router = APIRouter(prefix="/provisioning", tags=["provisioning"])

# Very simple global set for the gateway to connect and receive new jobs
# In production with multiple workers, this would use Redis pub/sub.
connected_gateways = set()

@router.websocket("/ws")
async def provisioning_ws(websocket: WebSocket):
    """
    WebSocket endpoint for the Gateway.
    The Gateway connects here to receive real-time notifications about new provisioning jobs.
    """
    # Note: In a real app we'd authenticate the gateway here (e.g. via token).
    await websocket.accept()
    connected_gateways.add(websocket)
    try:
        while True:
            # Keep connection alive
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        connected_gateways.remove(websocket)

async def notify_gateways():
    """Notify all connected gateways that a new job is available."""
    for ws in list(connected_gateways):
        try:
            await ws.send_json({"event": "new_job_available"})
        except Exception:
            connected_gateways.discard(ws)

@router.post("/jobs", response_model=ProvisioningJobResponse, status_code=status.HTTP_201_CREATED)
async def create_provisioning_job(
    job_in: ProvisioningJobCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Create a new provisioning job from the Dashboard."""
    job = ProvisioningJob(
        ssid=job_in.ssid,
        password=job_in.password,
        pairing_code=job_in.pairing_code,
        status=ProvisioningStatus.PENDING
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # Notify connected gateways via WebSocket
    asyncio.create_task(notify_gateways())

    return job

@router.get("/jobs/pending", response_model=list[ProvisioningJobResponse])
def get_pending_jobs(db: Session = Depends(get_db)):
    """
    Gateway polls this endpoint (fallback) to get all pending jobs.
    """
    # Note: Gateway auth is usually required here.
    jobs = db.query(ProvisioningJob).filter(ProvisioningJob.status == ProvisioningStatus.PENDING).all()
    return jobs

@router.patch("/jobs/{job_id}", response_model=ProvisioningJobResponse)
def update_provisioning_job(
    job_id: UUID4,
    job_in: ProvisioningJobUpdate,
    db: Session = Depends(get_db)
):
    """Gateway calls this to update the job status after provisioning."""
    job = db.query(ProvisioningJob).filter(ProvisioningJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    job.status = job_in.status
    if job_in.mac_address:
        job.mac_address = job_in.mac_address

    db.commit()
    db.refresh(job)
    return job
