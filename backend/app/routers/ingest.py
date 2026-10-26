"""Durable gateway ingestion; SQL and password checks never block the event loop."""

import asyncio
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from time import perf_counter
from weakref import WeakKeyDictionary

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from fastapi.routing import APIRoute
from prometheus_client import Histogram
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.database import get_db
from app.gateway_auth import authenticate_gateway_api_key
from app.models.ingest_log import IngestLog
from app.routers.ws import manager
from app.schemas.ingest import IngestRequest, IngestResponse
from app.services.ingest_service import DuplicateIngestionError, process_ingestion
from app.services.notification_service import notification_service

_large_request_slots = WeakKeyDictionary()
_large_upload_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ingest-bulk")


class BoundedIngestRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded(request):
            length = request.headers.get("content-length", "")
            if length.isdigit() and int(length) <= 65536:
                return await handler(request)
            # Queue before reading/parsing large or streamed bodies. Uvicorn's
            # receive backpressure bounds queued body memory, with no rejection.
            loop = asyncio.get_running_loop()
            slot = _large_request_slots.setdefault(loop, asyncio.Semaphore(1))
            async with slot:
                return await handler(request)

        return bounded


router = APIRouter(prefix="/ingest", tags=["ingest"], route_class=BoundedIngestRoute)
logger = logging.getLogger(__name__)
INGEST_STAGE = Histogram(
    "greenmind_ingest_stage_seconds",
    "Gateway ingestion stage duration",
    ["stage"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.055, 0.1, 0.25, 0.55, 1, 2, 5),
)


def _persist(data, api_key, bind):
    # Own the session completely within this worker thread. Nothing lazy-loaded
    # or ORM-backed crosses into the async response/notification path.
    with Session(bind=bind, autoflush=False) as db:
        with INGEST_STAGE.labels("authenticate").time():
            gateway = authenticate_gateway_api_key(db, api_key)
        if gateway.hardware_id != data.gateway_serial:
            raise HTTPException(status_code=403, detail="Gateway identity mismatch")
        gateway_id = str(gateway.id)
        zone_id = str(gateway.zone_id) if gateway.zone_id else None
        sensor_ids = {}
        try:
            with INGEST_STAGE.labels("persist").time():
                ingested, alerts = process_ingestion(data, gateway, db, sensor_ids=sensor_ids)
        except DuplicateIngestionError:
            return "duplicate", 0, gateway_id, None, [], [], []
        except IntegrityError:
            # Concurrent retries may both see no log before either commits.
            # Only a committed success belonging to this gateway is an ACK.
            db.rollback()
            receipt = db.query(IngestLog).filter_by(measurement_id=data.measurement_id).first()
            if receipt and receipt.status == "success":
                if str(receipt.gateway_id) != gateway_id:
                    raise HTTPException(409, "Measurement ID belongs to another gateway") from None
                return "duplicate", 0, gateway_id, None, [], [], []
            raise
        now = datetime.now(UTC)
        readings_out = []
        grouped = defaultdict(list)
        for reading in data.readings:
            stamp = reading.timestamp.isoformat() if reading.timestamp else now.isoformat()
            readings_out.append(
                {
                    "sensor_mac": reading.sensor_mac,
                    "sensor_kind": reading.sensor_kind,
                    "value": reading.value,
                    "unit": reading.unit,
                    "timestamp": stamp,
                }
            )
            grouped[reading.sensor_mac].append(
                {
                    "value": reading.value,
                    "unit": reading.unit,
                    "kind": reading.sensor_kind,
                    "timestamp": stamp,
                }
            )
        sensor_messages = [
            {
                "event": "live_reading",
                "sensor_id": sensor_ids[mac],
                "sensor_mac": mac,
                "readings": values,
            }
            for mac, values in grouped.items()
        ]
        return "success", ingested, gateway_id, zone_id, alerts, readings_out, sensor_messages


async def _broadcast(zone_id, gateway_id, measurement_id, readings, sensor_messages):
    # The committed upload response has already been sent. Subscriber timeout
    # and current tenant authorization continue to be enforced by the manager.
    try:
        if zone_id and manager.active_connections.get(zone_id):
            await manager.broadcast_to_zone(
                {
                    "event": "new_readings",
                    "gateway_id": gateway_id,
                    "measurement_id": measurement_id,
                    "readings": readings,
                },
                zone_id,
            )
        for message in sensor_messages:
            if manager.sensor_connections.get(message["sensor_id"]):
                await manager.broadcast_to_sensor(message, message["sensor_id"])
    except Exception as exc:
        logger.warning("ingest_live_notification_failed type=%s", type(exc).__name__)


@router.post("", response_model=IngestResponse, status_code=201)
async def ingest_data(
    data: IngestRequest,
    background_tasks: BackgroundTasks,
    x_api_key: str | None = Header(None, alias="X-Api-Key"),
    db: Session = Depends(get_db),
):
    started = perf_counter()
    if len(data.readings) > 500:
        # Reuse one worker's SQL allocator arenas for large requests. Ordinary
        # reports retain the shared worker pool and cannot queue behind a bulk.
        result = await asyncio.get_running_loop().run_in_executor(
            _large_upload_worker, _persist, data, x_api_key, db.get_bind()
        )
    else:
        result = await run_in_threadpool(_persist, data, x_api_key, db.get_bind())
    status, ingested, gateway_id, zone_id, alerts, readings, messages = result
    for alert in alerts:
        background_tasks.add_task(
            notification_service.send_electrode_disconnect_alert,
            **alert,
        )
    if status == "success":
        background_tasks.add_task(
            _broadcast,
            zone_id,
            gateway_id,
            str(data.measurement_id),
            readings,
            messages,
        )
    elapsed = perf_counter() - started
    INGEST_STAGE.labels("response").observe(elapsed)
    if elapsed >= 0.1:
        logger.warning(
            "gateway_ingest_slow duration_ms=%.1f readings=%d", elapsed * 1000, len(data.readings)
        )
    return IngestResponse(
        status=status,
        ingested=ingested,
        gateway_id=gateway_id,
        measurement_id=str(data.measurement_id),
    )
