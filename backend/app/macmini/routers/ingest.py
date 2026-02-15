"""Ingestion endpoints: plant-signal-1hz, env, events – idempotent batch inserts."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.macmini.auth import require_ingest_auth
from app.macmini.database import get_db
from app.macmini.schemas import (
    EnvIngestRequest,
    EventIngestRequest,
    IngestResponse,
    PlantSignalIngestRequest,
)
from app.models import IngestLog

logger = logging.getLogger("greenmind.ingest")

router = APIRouter(prefix="/v1/ingest", tags=["ingest"])


def _claim_request_id(
    db: Session, request_id: UUID, endpoint: str, source: str | None,
    greenhouse_id: UUID | None = None,
) -> bool:
    """Try to claim a request_id. Returns True if new, False if duplicate."""
    existing = db.query(IngestLog).filter(IngestLog.request_id == request_id).first()
    if existing:
        return False
    log = IngestLog(
        request_id=request_id,
        endpoint=endpoint,
        source=source or "unknown",
        greenhouse_id=greenhouse_id,
        status="received",
    )
    db.add(log)
    db.commit()
    return True


def _update_ingest_log(db: Session, request_id: UUID, status_val: str, details: dict[str, Any]) -> None:
    log = db.query(IngestLog).filter(IngestLog.request_id == request_id).first()
    if log:
        log.status = status_val
        log.details = details
        db.commit()


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _check_timestamp_drift(timestamp: datetime) -> str | None:
    diff = abs((datetime.now(timezone.utc) - _utc(timestamp)).total_seconds())
    if diff > 86400:
        return f"timestamp_drift={diff:.0f}s"
    return None


# ── Plant Signal 1Hz ──────────────────────────────────────────

@router.post("/plant-signal-1hz", response_model=IngestResponse)
def ingest_plant_signal(
    request: Request,
    payload: PlantSignalIngestRequest,
    source_device: str | None = Header(default=None, alias="X-Source-Device"),
    db: Session = Depends(get_db),
    _: None = Depends(require_ingest_auth),
):
    if not _claim_request_id(db, payload.request_id, "plant-signal-1hz", source_device, payload.greenhouse_id):
        return IngestResponse(request_id=payload.request_id, status="duplicate", inserted_rows=0)

    try:
        start = _utc(payload.start_time)
        drift_flag = _check_timestamp_drift(start)

        rows = []
        for i, val in enumerate(payload.values_uV):
            ts = start + timedelta(seconds=i * payload.dt_seconds)
            q = payload.quality[i] if payload.quality else 0
            meta = {}
            if drift_flag:
                meta["warning"] = drift_flag
            rows.append({
                "time": ts, "greenhouse_id": str(payload.greenhouse_id),
                "plant_id": str(payload.plant_id), "sensor_id": str(payload.sensor_id),
                "value_uv": val, "quality": q, "meta": "{}",
            })

        if rows:
            db.execute(
                text(
                    "INSERT INTO plant_signal_1hz (time, greenhouse_id, plant_id, sensor_id, value_uv, quality, meta) "
                    "VALUES (:time, :greenhouse_id, :plant_id, :sensor_id, :value_uv, :quality, :meta::jsonb) "
                    "ON CONFLICT DO NOTHING"
                ),
                rows,
            )
            db.commit()

        inserted = len(rows)
        _update_ingest_log(db, payload.request_id, "ingested", {"inserted_rows": inserted})
        return IngestResponse(request_id=payload.request_id, status="ingested", inserted_rows=inserted)

    except Exception as e:
        db.rollback()
        _update_ingest_log(db, payload.request_id, "error", {"error": str(e)})
        logger.exception("ingest_plant_signal failed")
        raise HTTPException(status_code=500, detail="Ingestion failed")


# ── Environmental Measurements ────────────────────────────────

@router.post("/env", response_model=IngestResponse)
def ingest_env(
    request: Request,
    payload: EnvIngestRequest,
    source_device: str | None = Header(default=None, alias="X-Source-Device"),
    db: Session = Depends(get_db),
    _: None = Depends(require_ingest_auth),
):
    gh_id = payload.measurements[0].greenhouse_id if payload.measurements else None
    if not _claim_request_id(db, payload.request_id, "env", source_device, gh_id):
        return IngestResponse(request_id=payload.request_id, status="duplicate", inserted_rows=0)

    try:
        rows = []
        for m in payload.measurements:
            rows.append({
                "time": _utc(m.time), "greenhouse_id": str(m.greenhouse_id),
                "sensor_id": str(m.sensor_id), "value": m.value,
                "quality": m.quality, "meta": "{}",
            })

        if rows:
            db.execute(
                text(
                    "INSERT INTO env_measurement (time, greenhouse_id, sensor_id, value, quality, meta) "
                    "VALUES (:time, :greenhouse_id, :sensor_id, :value, :quality, :meta::jsonb) "
                    "ON CONFLICT DO NOTHING"
                ),
                rows,
            )
            db.commit()

        inserted = len(rows)
        _update_ingest_log(db, payload.request_id, "ingested", {"inserted_rows": inserted})
        return IngestResponse(request_id=payload.request_id, status="ingested", inserted_rows=inserted)

    except Exception as e:
        db.rollback()
        _update_ingest_log(db, payload.request_id, "error", {"error": str(e)})
        logger.exception("ingest_env failed")
        raise HTTPException(status_code=500, detail="Ingestion failed")


# ── Events ────────────────────────────────────────────────────

@router.post("/events", response_model=IngestResponse)
def ingest_event(
    request: Request,
    payload: EventIngestRequest,
    source_device: str | None = Header(default=None, alias="X-Source-Device"),
    db: Session = Depends(get_db),
    _: None = Depends(require_ingest_auth),
):
    if not _claim_request_id(db, payload.request_id, "events", source_device, payload.greenhouse_id):
        return IngestResponse(request_id=payload.request_id, status="duplicate", inserted_rows=0)

    try:
        db.execute(
            text(
                "INSERT INTO event_log (id, greenhouse_id, time, type, payload, source_device_id, request_id) "
                "VALUES (gen_random_uuid(), :greenhouse_id, :time, :type, :payload::jsonb, :source_device_id, :request_id) "
                "ON CONFLICT DO NOTHING"
            ),
            {
                "greenhouse_id": str(payload.greenhouse_id),
                "time": _utc(payload.time),
                "type": payload.type,
                "payload": "{}",
                "source_device_id": source_device,
                "request_id": str(payload.request_id),
            },
        )
        db.commit()
        _update_ingest_log(db, payload.request_id, "ingested", {"inserted_rows": 1})
        return IngestResponse(request_id=payload.request_id, status="ingested", inserted_rows=1)

    except Exception as e:
        db.rollback()
        _update_ingest_log(db, payload.request_id, "error", {"error": str(e)})
        logger.exception("ingest_event failed")
        raise HTTPException(status_code=500, detail="Ingestion failed")
