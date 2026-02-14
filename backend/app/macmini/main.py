import hashlib
import io
import json
import logging
import time
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

import os

import pyarrow as pa
import pyarrow.parquet as pq
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from app.macmini.auth import require_ingest_auth, require_read_auth
from app.macmini.config import get_settings
from app.macmini.database import get_db
from app.macmini.schemas import (
    EnvIngestRequest,
    EventIngestRequest,
    EventOut,
    ExportRequest,
    ExportResponse,
    ExportStatusResponse,
    HealthResponse,
    IngestResponse,
    PlantSignalIngestRequest,
)
from app.macmini.storage import ensure_bucket_exists, get_s3_client, open_object_stream, upload_bytes

settings = get_settings()
logger = logging.getLogger("greenmind.macmini")
logging.basicConfig(level=settings.log_level.upper())

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="GreenMindDB Mac mini API",
    version="1.0.0",
    description="Mac-mini local stack API for ingestion, querying and ML export datasets.",
)

# CORS – allow frontend to reach the API
_cors_raw = os.environ.get("CORS_ORIGINS", '["http://localhost:3000"]')
try:
    _cors_origins = json.loads(_cors_raw) if _cors_raw.startswith("[") else [o.strip() for o in _cors_raw.split(",")]
except Exception:
    _cors_origins = ["http://localhost:3000"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _check_timestamp_drift(timestamp: datetime) -> bool:
    ts = _utc(timestamp)
    now = datetime.now(timezone.utc)
    return ts > now + timedelta(minutes=5) or ts < now - timedelta(hours=24)


def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, default=str)


def _claim_request_id(
    db: Session,
    request_id: UUID,
    endpoint: str,
    source: str,
    details: dict[str, Any] | None = None,
) -> bool:
    details_payload = _json(details or {})
    row = db.execute(
        text(
            """
            INSERT INTO ingest_log (request_id, endpoint, source, status, details)
            VALUES (:request_id, :endpoint, :source, 'received', CAST(:details AS jsonb))
            ON CONFLICT (request_id) DO NOTHING
            RETURNING request_id
            """
        ),
        {
            "request_id": str(request_id),
            "endpoint": endpoint,
            "source": source,
            "details": details_payload,
        },
    ).first()
    return row is not None


def _update_ingest_log(db: Session, request_id: UUID, status_value: str, details: dict[str, Any]) -> None:
    db.execute(
        text(
            """
            UPDATE ingest_log
            SET status = :status,
                details = COALESCE(details, '{}'::jsonb) || CAST(:details AS jsonb)
            WHERE request_id = :request_id
            """
        ),
        {
            "request_id": str(request_id),
            "status": status_value,
            "details": _json(details),
        },
    )


def _require_non_empty(value: str | None, name: str) -> str:
    if not value:
        raise HTTPException(status_code=400, detail=f"{name} must not be empty")
    return value


def _row_exists(db: Session, table: str, entity_id: UUID) -> bool:
    row = db.execute(
        text(f"SELECT 1 FROM {table} WHERE id = :id LIMIT 1"),
        {"id": str(entity_id)},
    ).first()
    return row is not None


@app.middleware("http")
async def structured_logging_middleware(request: Request, call_next):
    start = time.perf_counter()
    req_id = request.headers.get("X-Request-ID", str(uuid4()))
    request.state.request_id = req_id
    request.state.source = request.headers.get("X-Source-Device")

    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = str(request.state.request_id)
        return response
    finally:
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        payload = {
            "request_id": str(getattr(request.state, "request_id", req_id)),
            "path": request.url.path,
            "method": request.method,
            "status": status_code,
            "duration_ms": duration_ms,
            "source": getattr(request.state, "source", None),
        }
        logger.info(_json(payload))


@app.on_event("startup")
def startup_checks() -> None:
    ensure_bucket_exists()


Instrumentator().instrument(app).expose(app, include_in_schema=False, should_gzip=True)


@app.get("/health", response_model=HealthResponse)
def health(db: Session = Depends(get_db)):
    db_ok = "ok"
    minio_ok = "ok"

    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_ok = "error"

    try:
        get_s3_client().head_bucket(Bucket=settings.s3_bucket)
    except Exception:
        minio_ok = "error"

    if db_ok == "error" or minio_ok == "error":
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=HealthResponse(status="degraded", db=db_ok, minio=minio_ok).model_dump(),
        )

    return HealthResponse(status="ok", db=db_ok, minio=minio_ok)


@app.post("/v1/ingest/plant-signal-1hz", response_model=IngestResponse)
@limiter.limit(settings.ingest_rate_limit)
def ingest_plant_signal(
    request: Request,
    payload: PlantSignalIngestRequest,
    source_device: str | None = Header(default=None, alias="X-Source-Device"),
    db: Session = Depends(get_db),
    _: None = Depends(require_ingest_auth),
):
    request.state.request_id = str(payload.request_id)
    request.state.source = source_device or "unknown"

    if payload.quality is None:
        quality = [0] * len(payload.values_uV)
    else:
        quality = payload.quality

    endpoint = "/v1/ingest/plant-signal-1hz"
    source = source_device or "api"
    drift_flag = _check_timestamp_drift(payload.start_time)

    if not _row_exists(db, "plant", payload.plant_id):
        raise HTTPException(status_code=404, detail="plant_id not found")
    if not _row_exists(db, "sensor", payload.sensor_id):
        raise HTTPException(status_code=404, detail="sensor_id not found")

    if not _claim_request_id(
        db,
        payload.request_id,
        endpoint=endpoint,
        source=source,
        details={"timestamp_drift": drift_flag},
    ):
        return IngestResponse(request_id=payload.request_id, status="duplicate", inserted_rows=0)

    try:
        rows = []
        start_time = _utc(payload.start_time)
        for i, value_uv in enumerate(payload.values_uV):
            ts = start_time + timedelta(seconds=i * payload.dt_seconds)
            rows.append(
                {
                    "time": ts,
                    "plant_id": str(payload.plant_id),
                    "sensor_id": str(payload.sensor_id),
                    "value_uv": float(value_uv),
                    "quality": int(quality[i]),
                    "meta": _json({"request_id": str(payload.request_id), "dt_seconds": payload.dt_seconds}),
                }
            )

        db.execute(
            text(
                """
                INSERT INTO plant_signal_1hz (time, plant_id, sensor_id, value_uv, quality, meta)
                VALUES (:time, :plant_id, :sensor_id, :value_uv, :quality, CAST(:meta AS jsonb))
                ON CONFLICT DO NOTHING
                """
            ),
            rows,
        )

        _update_ingest_log(
            db,
            payload.request_id,
            "ingested",
            {"inserted_rows": len(rows), "timestamp_drift": drift_flag},
        )
        db.commit()
        return IngestResponse(request_id=payload.request_id, status="ingested", inserted_rows=len(rows))
    except Exception as exc:
        db.rollback()
        _update_ingest_log(db, payload.request_id, "failed", {"error": str(exc)})
        db.commit()
        raise HTTPException(status_code=500, detail="Failed to ingest plant signal") from exc


@app.post("/v1/ingest/env", response_model=IngestResponse)
@limiter.limit(settings.ingest_rate_limit)
def ingest_env(
    request: Request,
    payload: EnvIngestRequest,
    source_device: str | None = Header(default=None, alias="X-Source-Device"),
    db: Session = Depends(get_db),
    _: None = Depends(require_ingest_auth),
):
    request.state.request_id = str(payload.request_id)
    request.state.source = source_device or "unknown"

    endpoint = "/v1/ingest/env"
    source = source_device or "api"

    for measurement in payload.measurements:
        if not _row_exists(db, "sensor", measurement.sensor_id):
            raise HTTPException(status_code=404, detail=f"sensor_id not found: {measurement.sensor_id}")

    drift_count = sum(1 for m in payload.measurements if _check_timestamp_drift(m.time))

    if not _claim_request_id(
        db,
        payload.request_id,
        endpoint=endpoint,
        source=source,
        details={"drifted_measurements": drift_count},
    ):
        return IngestResponse(request_id=payload.request_id, status="duplicate", inserted_rows=0)

    try:
        rows = [
            {
                "time": _utc(m.time),
                "sensor_id": str(m.sensor_id),
                "value": float(m.value),
                "quality": int(m.quality),
                "meta": _json(m.meta),
            }
            for m in payload.measurements
        ]

        db.execute(
            text(
                """
                INSERT INTO env_measurement (time, sensor_id, value, quality, meta)
                VALUES (:time, :sensor_id, :value, :quality, CAST(:meta AS jsonb))
                ON CONFLICT DO NOTHING
                """
            ),
            rows,
        )

        _update_ingest_log(
            db,
            payload.request_id,
            "ingested",
            {"inserted_rows": len(rows), "drifted_measurements": drift_count},
        )
        db.commit()
        return IngestResponse(request_id=payload.request_id, status="ingested", inserted_rows=len(rows))
    except Exception as exc:
        db.rollback()
        _update_ingest_log(db, payload.request_id, "failed", {"error": str(exc)})
        db.commit()
        raise HTTPException(status_code=500, detail="Failed to ingest env measurements") from exc


@app.post("/v1/ingest/events", response_model=IngestResponse)
@limiter.limit(settings.ingest_rate_limit)
def ingest_event(
    request: Request,
    payload: EventIngestRequest,
    source_device: str | None = Header(default=None, alias="X-Source-Device"),
    db: Session = Depends(get_db),
    _: None = Depends(require_ingest_auth),
):
    request.state.request_id = str(payload.request_id)
    request.state.source = source_device or "unknown"

    endpoint = "/v1/ingest/events"
    source = source_device or "api"
    drift_flag = _check_timestamp_drift(payload.time)

    if not _row_exists(db, "greenhouse", payload.greenhouse_id):
        raise HTTPException(status_code=404, detail="greenhouse_id not found")

    if not _claim_request_id(
        db,
        payload.request_id,
        endpoint=endpoint,
        source=source,
        details={"timestamp_drift": drift_flag},
    ):
        return IngestResponse(request_id=payload.request_id, status="duplicate", inserted_rows=0)

    try:
        db.execute(
            text(
                """
                INSERT INTO event_log (
                    id, greenhouse_id, time, type, payload, created_by, source_device_id
                )
                VALUES (
                    :id, :greenhouse_id, :time, :type, CAST(:payload AS jsonb), :created_by, :source_device_id
                )
                """
            ),
            {
                "id": str(uuid4()),
                "greenhouse_id": str(payload.greenhouse_id),
                "time": _utc(payload.time),
                "type": payload.type,
                "payload": _json(payload.payload),
                "created_by": "ingest-token",
                "source_device_id": None,
            },
        )

        _update_ingest_log(
            db,
            payload.request_id,
            "ingested",
            {"inserted_rows": 1, "timestamp_drift": drift_flag},
        )
        db.commit()
        return IngestResponse(request_id=payload.request_id, status="ingested", inserted_rows=1)
    except Exception as exc:
        db.rollback()
        _update_ingest_log(db, payload.request_id, "failed", {"error": str(exc)})
        db.commit()
        raise HTTPException(status_code=500, detail="Failed to ingest event") from exc


def _default_from_to(
    from_ts: datetime | None,
    to_ts: datetime | None,
) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    start = _utc(from_ts) if from_ts else now - timedelta(hours=24)
    end = _utc(to_ts) if to_ts else now
    return start, end


_SIGNAL_AGG_VIEW = {
    "1m": "plant_signal_1hz_1m",
    "15m": "plant_signal_1hz_15m",
}

_ENV_AGG_VIEW = {
    "1m": "env_measurement_1m",
    "15m": "env_measurement_15m",
}


@app.get("/v1/plants/{plant_id}/signal")
def query_plant_signal(
    plant_id: UUID,
    from_ts: datetime | None = Query(default=None, alias="from"),
    to_ts: datetime | None = Query(default=None, alias="to"),
    agg: Literal["raw", "1m", "15m"] = Query(default="raw"),
    db: Session = Depends(get_db),
    _: None = Depends(require_read_auth),
):
    start, end = _default_from_to(from_ts, to_ts)

    if agg == "raw":
        rows = db.execute(
            text(
                """
                SELECT time, plant_id, sensor_id, value_uv, quality, meta
                FROM plant_signal_1hz
                WHERE plant_id = :plant_id
                  AND time >= :start_time
                  AND time <= :end_time
                ORDER BY time ASC
                """
            ),
            {"plant_id": str(plant_id), "start_time": start, "end_time": end},
        ).mappings().all()
        return {"agg": "raw", "points": [dict(r) for r in rows]}

    view_name = _SIGNAL_AGG_VIEW[agg]
    rows = db.execute(
        text(
            f"""
            SELECT bucket, plant_id, sensor_id, value_avg, value_min, value_max, sample_count
            FROM {view_name}
            WHERE plant_id = :plant_id
              AND bucket >= :start_time
              AND bucket <= :end_time
            ORDER BY bucket ASC
            """
        ),
        {"plant_id": str(plant_id), "start_time": start, "end_time": end},
    ).mappings().all()

    return {"agg": agg, "points": [dict(r) for r in rows]}


@app.get("/v1/sensors/{sensor_id}/env")
def query_sensor_env(
    sensor_id: UUID,
    from_ts: datetime | None = Query(default=None, alias="from"),
    to_ts: datetime | None = Query(default=None, alias="to"),
    agg: Literal["raw", "1m", "15m"] = Query(default="raw"),
    db: Session = Depends(get_db),
    _: None = Depends(require_read_auth),
):
    start, end = _default_from_to(from_ts, to_ts)

    if agg == "raw":
        rows = db.execute(
            text(
                """
                SELECT time, sensor_id, value, quality, meta
                FROM env_measurement
                WHERE sensor_id = :sensor_id
                  AND time >= :start_time
                  AND time <= :end_time
                ORDER BY time ASC
                """
            ),
            {"sensor_id": str(sensor_id), "start_time": start, "end_time": end},
        ).mappings().all()
        return {"agg": "raw", "points": [dict(r) for r in rows]}

    view_name = _ENV_AGG_VIEW[agg]
    rows = db.execute(
        text(
            f"""
            SELECT bucket, sensor_id, value_avg, value_min, value_max, sample_count
            FROM {view_name}
            WHERE sensor_id = :sensor_id
              AND bucket >= :start_time
              AND bucket <= :end_time
            ORDER BY bucket ASC
            """
        ),
        {"sensor_id": str(sensor_id), "start_time": start, "end_time": end},
    ).mappings().all()

    return {"agg": agg, "points": [dict(r) for r in rows]}


@app.get("/v1/events", response_model=list[EventOut])
def query_events(
    from_ts: datetime | None = Query(default=None, alias="from"),
    to_ts: datetime | None = Query(default=None, alias="to"),
    type: str | None = Query(default=None),
    greenhouse_id: UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    _: None = Depends(require_read_auth),
):
    start, end = _default_from_to(from_ts, to_ts)

    sql = """
        SELECT id, greenhouse_id, time, type, payload, created_by, source_device_id
        FROM event_log
        WHERE time >= :start_time
          AND time <= :end_time
    """
    params: dict[str, Any] = {"start_time": start, "end_time": end}

    if type:
        sql += " AND type = :type"
        params["type"] = type

    if greenhouse_id:
        sql += " AND greenhouse_id = :greenhouse_id"
        params["greenhouse_id"] = str(greenhouse_id)

    sql += " ORDER BY time ASC"
    rows = db.execute(text(sql), params).mappings().all()
    return [EventOut(**dict(row)) for row in rows]


def _serialize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for row in rows:
        normalized: dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, UUID):
                normalized[key] = str(value)
            elif isinstance(value, (dict, list)):
                normalized[key] = json.dumps(value, sort_keys=True)
            else:
                normalized[key] = value
        serialized.append(normalized)
    return serialized


def _to_parquet_bytes(rows: list[dict[str, Any]]) -> bytes:
    table = pa.Table.from_pylist(rows)
    sink = io.BytesIO()
    pq.write_table(table, sink, compression="zstd")
    return sink.getvalue()


@app.post("/v1/exports/dataset", response_model=ExportResponse)
def create_export(
    payload: ExportRequest,
    db: Session = Depends(get_db),
    _: None = Depends(require_read_auth),
):
    export_id = uuid4()
    now = datetime.now(timezone.utc)

    db.execute(
        text(
            """
            INSERT INTO export_job (
                id, greenhouse_id, plant_ids, from_time, to_time, include_env, include_events,
                resample, status, created_at
            )
            VALUES (
                :id, :greenhouse_id, CAST(:plant_ids AS jsonb), :from_time, :to_time,
                :include_env, :include_events, :resample, 'running', :created_at
            )
            """
        ),
        {
            "id": str(export_id),
            "greenhouse_id": str(payload.greenhouse_id),
            "plant_ids": _json([str(p) for p in (payload.plant_ids or [])]),
            "from_time": _utc(payload.from_time),
            "to_time": _utc(payload.to_time),
            "include_env": payload.include_env,
            "include_events": payload.include_events,
            "resample": payload.resample,
            "created_at": now,
        },
    )
    db.commit()

    try:
        start_time = _utc(payload.from_time)
        end_time = _utc(payload.to_time)

        filter_clause = ""
        params: dict[str, Any] = {"from_time": start_time, "to_time": end_time, "greenhouse_id": str(payload.greenhouse_id)}

        if payload.plant_ids:
            filter_clause = "AND plant_id IN :plant_ids"
            params["plant_ids"] = tuple(str(p) for p in payload.plant_ids)

        if payload.resample == "raw":
            signal_query = text(
                f"""
                SELECT time, plant_id, sensor_id, value_uv, quality, meta
                FROM plant_signal_1hz
                WHERE time >= :from_time AND time <= :to_time
                  AND plant_id IN (
                      SELECT p.id
                      FROM plant p
                      JOIN zone z ON z.id = p.zone_id
                      WHERE z.greenhouse_id = :greenhouse_id
                  )
                {filter_clause}
                ORDER BY time ASC
                """
            )
        else:
            signal_view = _SIGNAL_AGG_VIEW[payload.resample]
            signal_query = text(
                f"""
                SELECT bucket AS time, plant_id, sensor_id, value_avg AS value_uv,
                       0::smallint AS quality,
                       jsonb_build_object(
                         'value_min', value_min,
                         'value_max', value_max,
                         'sample_count', sample_count,
                         'agg', :agg
                       ) AS meta
                FROM {signal_view}
                WHERE bucket >= :from_time AND bucket <= :to_time
                  AND plant_id IN (
                      SELECT p.id
                      FROM plant p
                      JOIN zone z ON z.id = p.zone_id
                      WHERE z.greenhouse_id = :greenhouse_id
                  )
                {filter_clause}
                ORDER BY bucket ASC
                """
            )
            params["agg"] = payload.resample

        if payload.plant_ids:
            signal_query = signal_query.bindparams(bindparam("plant_ids", expanding=True))

        signal_rows = _serialize_rows([dict(r) for r in db.execute(signal_query, params).mappings().all()])

        env_rows: list[dict[str, Any]] = []
        if payload.include_env:
            env_query = text(
                """
                SELECT em.time, em.sensor_id, em.value, em.quality, em.meta
                FROM env_measurement em
                JOIN sensor s ON s.id = em.sensor_id
                JOIN device d ON d.id = s.device_id
                WHERE em.time >= :from_time
                  AND em.time <= :to_time
                  AND d.greenhouse_id = :greenhouse_id
                ORDER BY em.time ASC
                """
            )
            env_rows = _serialize_rows([dict(r) for r in db.execute(env_query, params).mappings().all()])

        event_rows: list[dict[str, Any]] = []
        if payload.include_events:
            event_query = text(
                """
                SELECT id, greenhouse_id, time, type, payload, created_by, source_device_id
                FROM event_log
                WHERE greenhouse_id = :greenhouse_id
                  AND time >= :from_time
                  AND time <= :to_time
                ORDER BY time ASC
                """
            )
            event_rows = _serialize_rows([dict(r) for r in db.execute(event_query, params).mappings().all()])

        schema_json = {
            "version": "1.0",
            "greenhouse_id": str(payload.greenhouse_id),
            "time_range": {"from": start_time.isoformat(), "to": end_time.isoformat()},
            "resample": payload.resample,
            "include_env": payload.include_env,
            "include_events": payload.include_events,
            "datasets": {
                "plant_signal": {
                    "rows": len(signal_rows),
                    "columns": ["time", "plant_id", "sensor_id", "value_uv", "quality", "meta"],
                    "units": {"value_uv": "uV"},
                },
                "env_measurement": {
                    "rows": len(env_rows),
                    "columns": ["time", "sensor_id", "value", "quality", "meta"],
                },
                "event_log": {
                    "rows": len(event_rows),
                    "columns": ["id", "greenhouse_id", "time", "type", "payload", "created_by", "source_device_id"],
                },
            },
        }

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("schema.json", json.dumps(schema_json, indent=2))
            archive.writestr("plant_signal.parquet", _to_parquet_bytes(signal_rows))
            if payload.include_env:
                archive.writestr("env_measurement.parquet", _to_parquet_bytes(env_rows))
            if payload.include_events:
                archive.writestr("event_log.parquet", _to_parquet_bytes(event_rows))

        zip_payload = zip_buffer.getvalue()
        digest = hashlib.sha256(zip_payload).hexdigest()
        storage_key = f"exports/{export_id}/dataset.zip"

        upload_bytes(storage_key, zip_payload, "application/zip")

        db.execute(
            text(
                """
                INSERT INTO object_meta (
                    id, time, greenhouse_id, kind, storage_key, content_type, sha256, size_bytes
                )
                VALUES (
                    :id, :time, :greenhouse_id, :kind, :storage_key, :content_type, :sha256, :size_bytes
                )
                """
            ),
            {
                "id": str(uuid4()),
                "time": datetime.now(timezone.utc),
                "greenhouse_id": str(payload.greenhouse_id),
                "kind": "dataset_export",
                "storage_key": storage_key,
                "content_type": "application/zip",
                "sha256": digest,
                "size_bytes": len(zip_payload),
            },
        )

        db.execute(
            text(
                """
                UPDATE export_job
                SET status = 'completed',
                    storage_key = :storage_key,
                    completed_at = :completed_at,
                    error = NULL
                WHERE id = :id
                """
            ),
            {
                "id": str(export_id),
                "storage_key": storage_key,
                "completed_at": datetime.now(timezone.utc),
            },
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        db.execute(
            text(
                """
                UPDATE export_job
                SET status = 'failed', error = :error, completed_at = :completed_at
                WHERE id = :id
                """
            ),
            {
                "id": str(export_id),
                "error": str(exc),
                "completed_at": datetime.now(timezone.utc),
            },
        )
        db.commit()
        raise HTTPException(status_code=500, detail="Failed to create export") from exc

    return ExportResponse(export_id=export_id)


@app.get("/v1/exports/{export_id}/status", response_model=ExportStatusResponse)
def export_status(
    export_id: UUID,
    db: Session = Depends(get_db),
    _: None = Depends(require_read_auth),
):
    row = db.execute(
        text(
            """
            SELECT id, status, storage_key, error, created_at, completed_at
            FROM export_job
            WHERE id = :id
            """
        ),
        {"id": str(export_id)},
    ).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="Export not found")

    return ExportStatusResponse(
        export_id=row["id"],
        status=row["status"],
        storage_key=row["storage_key"],
        error=row["error"],
        created_at=row["created_at"],
        completed_at=row["completed_at"],
    )


@app.get("/v1/exports/{export_id}/download")
def download_export(
    export_id: UUID,
    db: Session = Depends(get_db),
    _: None = Depends(require_read_auth),
):
    row = db.execute(
        text("SELECT status, storage_key FROM export_job WHERE id = :id"),
        {"id": str(export_id)},
    ).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="Export not found")

    if row["status"] != "completed" or not row["storage_key"]:
        raise HTTPException(status_code=409, detail="Export not ready")

    key = row["storage_key"]
    obj = open_object_stream(key)
    body = obj["Body"]

    return StreamingResponse(
        body.iter_chunks(chunk_size=1024 * 64),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={export_id}.zip"},
    )


# ──────────────────────────────────────────────
# Dashboard endpoints (read-only, no auth)
# ──────────────────────────────────────────────

@app.get("/v1/dashboard/overview")
def dashboard_overview(db: Session = Depends(get_db)):
    """Counts and stats for the dashboard overview cards."""
    counts = db.execute(
        text(
            """
            SELECT
                (SELECT count(*) FROM greenhouse) AS greenhouses,
                (SELECT count(*) FROM device) AS devices,
                (SELECT count(*) FROM sensor) AS sensors,
                (SELECT count(*) FROM plant) AS plants,
                (SELECT count(*) FROM plant_signal_1hz
                 WHERE time >= now() - INTERVAL '24 hours') AS signal_rows_24h,
                (SELECT count(*) FROM env_measurement
                 WHERE time >= now() - INTERVAL '24 hours') AS env_rows_24h,
                (SELECT count(*) FROM ingest_log
                 WHERE received_at >= now() - INTERVAL '24 hours') AS ingests_24h
            """
        )
    ).mappings().first()
    return dict(counts) if counts else {}


@app.get("/v1/dashboard/devices")
def dashboard_devices(db: Session = Depends(get_db)):
    """List all devices with their sensor count."""
    rows = db.execute(
        text(
            """
            SELECT
                d.id, d.serial, d.type, d.fw_version, d.last_seen, d.status,
                d.greenhouse_id,
                g.name AS greenhouse_name,
                (SELECT count(*) FROM sensor s WHERE s.device_id = d.id) AS sensor_count
            FROM device d
            LEFT JOIN greenhouse g ON g.id = d.greenhouse_id
            ORDER BY d.last_seen DESC NULLS LAST
            """
        )
    ).mappings().all()
    return [dict(r) for r in rows]


@app.get("/v1/dashboard/ingest-log")
def dashboard_ingest_log(
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Recent ingestion log entries."""
    rows = db.execute(
        text(
            """
            SELECT request_id, received_at, endpoint, source, status, details
            FROM ingest_log
            ORDER BY received_at DESC
            LIMIT :limit
            """
        ),
        {"limit": limit},
    ).mappings().all()
    return [dict(r) for r in rows]


@app.get("/v1/dashboard/greenhouses")
def dashboard_greenhouses(db: Session = Depends(get_db)):
    """List greenhouses with zone, device and plant counts."""
    rows = db.execute(
        text(
            """
            SELECT
                gh.id, gh.name, gh.location, gh.timezone,
                (SELECT count(*) FROM zone z WHERE z.greenhouse_id = gh.id) AS zone_count,
                (SELECT count(*) FROM device d WHERE d.greenhouse_id = gh.id) AS device_count,
                (SELECT count(*) FROM plant p
                 JOIN zone z ON z.id = p.zone_id
                 WHERE z.greenhouse_id = gh.id) AS plant_count
            FROM greenhouse gh
            ORDER BY gh.name
            """
        )
    ).mappings().all()
    return [dict(r) for r in rows]
