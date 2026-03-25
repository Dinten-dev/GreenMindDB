"""IoT data ingestion endpoint – gateways push sensor readings here."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.auth import verify_password
from app.database import get_db
from app.models.master import Gateway
from app.routers.ws import manager
from app.schemas.ingest import IngestRequest, IngestResponse
from app.services.ingest_service import DuplicateIngestionError, process_ingestion

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("", response_model=IngestResponse, status_code=201)
async def ingest_data(
    data: IngestRequest,
    db: Session = Depends(get_db),
    x_api_key: str | None = Header(None),
):
    """
    Ingest sensor readings from a gateway.
    Authenticate via X-Api-Key header (the key returned from gateway registration).
    """
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-Api-Key header")

    # Find gateway by hardware serial
    gateway = db.query(Gateway).filter(Gateway.hardware_id == data.gateway_serial).first()
    if not gateway:
        raise HTTPException(status_code=404, detail="Gateway not found")
    if not gateway.is_active:
        raise HTTPException(status_code=403, detail="Gateway is deactivated")
    if not gateway.api_key_hash:
        raise HTTPException(status_code=403, detail="Gateway has no API key")

    # Verify API key
    if not verify_password(x_api_key, gateway.api_key_hash):
        raise HTTPException(status_code=401, detail="Invalid API key")

    try:
        ingested = process_ingestion(data, gateway, db)
    except DuplicateIngestionError:
        return IngestResponse(
            status="duplicate",
            ingested=0,
            gateway_id=str(gateway.id),
            measurement_id=data.measurement_id,
        )

    # Broadcast real-time update
    if gateway.greenhouse_id:
        now = datetime.now(UTC)
        readings_out = [
            {
                "sensor_mac": r.sensor_mac,
                "sensor_kind": r.sensor_kind,
                "value": r.value,
                "unit": r.unit,
                "timestamp": r.timestamp or now.isoformat(),
            }
            for r in data.readings
        ]
        await manager.broadcast_to_greenhouse(
            {
                "event": "new_readings",
                "gateway_id": str(gateway.id),
                "measurement_id": data.measurement_id,
                "readings": readings_out,
            },
            str(gateway.greenhouse_id),
        )

    return IngestResponse(
        status="success",
        ingested=ingested,
        gateway_id=str(gateway.id),
        measurement_id=data.measurement_id,
    )
