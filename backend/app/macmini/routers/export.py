"""Export endpoints: create ML-ready dataset (Parquet), check status, download."""
import hashlib
import io
import json
import logging
import zipfile
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import pyarrow as pa
import pyarrow.parquet as pq
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.macmini.auth import get_current_user, resolve_greenhouse_id
from app.macmini.database import get_db
from app.macmini.config import get_settings
from app.macmini.storage import get_s3_client
from app.macmini.schemas import ExportRequest, ExportResponse, ExportStatusResponse
from app.models import ExportJob, User

logger = logging.getLogger("greenmind.export")
settings = get_settings()

router = APIRouter(prefix="/v1/exports", tags=["export"])


def _serialize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        row = {}
        for k, v in r.items():
            if isinstance(v, datetime):
                row[k] = v.isoformat()
            elif isinstance(v, UUID):
                row[k] = str(v)
            else:
                row[k] = v
        out.append(row)
    return out


def _to_parquet_bytes(rows: list[dict[str, Any]]) -> bytes:
    if not rows:
        return b""
    table = pa.Table.from_pylist(_serialize_rows(rows))
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


@router.post("/dataset", response_model=ExportResponse)
def create_export(
    payload: ExportRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    gh_id = resolve_greenhouse_id(user, payload.greenhouse_id)
    if not gh_id:
        raise HTTPException(status_code=400, detail="greenhouse_id required")

    job = ExportJob(
        greenhouse_id=gh_id,
        requested_by_user_id=user.id,
        status="running",
        params=payload.model_dump(mode="json"),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    try:
        # Query plant signals
        plant_filter = ""
        params: dict[str, Any] = {"gid": str(gh_id), "f": payload.from_time, "t": payload.to_time}
        if payload.plant_ids:
            ids_str = ",".join(f"'{str(p)}'" for p in payload.plant_ids)
            plant_filter = f"AND plant_id IN ({ids_str})"

        signal_rows = db.execute(
            text(
                "SELECT time, greenhouse_id, plant_id, sensor_id, value_uv, quality, meta "
                f"FROM plant_signal_1hz WHERE greenhouse_id = :gid AND time >= :f AND time < :t {plant_filter} "
                "ORDER BY time LIMIT 1000000"
            ),
            params,
        ).mappings().all()

        archive_buf = io.BytesIO()
        total_rows = 0

        with zipfile.ZipFile(archive_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            # Plant signals
            sig_data = [dict(r) for r in signal_rows]
            total_rows += len(sig_data)
            zf.writestr("plant_signal.parquet", _to_parquet_bytes(sig_data))

            # Env measurements
            if payload.include_env:
                env_rows = db.execute(
                    text(
                        "SELECT time, greenhouse_id, sensor_id, value, quality, meta "
                        "FROM env_measurement WHERE greenhouse_id = :gid AND time >= :f AND time < :t "
                        "ORDER BY time LIMIT 1000000"
                    ),
                    params,
                ).mappings().all()
                env_data = [dict(r) for r in env_rows]
                total_rows += len(env_data)
                zf.writestr("env_measurement.parquet", _to_parquet_bytes(env_data))

            # Events
            if payload.include_events:
                evt_rows = db.execute(
                    text(
                        "SELECT id, greenhouse_id, time, type, payload, created_by_user_id, source_device_id, request_id "
                        "FROM event_log WHERE greenhouse_id = :gid AND time >= :f AND time < :t "
                        "ORDER BY time LIMIT 100000"
                    ),
                    params,
                ).mappings().all()
                evt_data = [dict(r) for r in evt_rows]
                total_rows += len(evt_data)
                zf.writestr("events.parquet", _to_parquet_bytes(evt_data))

            # Ground truth
            if payload.include_ground_truth:
                gt_rows = db.execute(
                    text(
                        "SELECT id, greenhouse_id, plant_id, date, vitality_score, growth_score, pest_score, disease_score, notes "
                        "FROM ground_truth_daily WHERE greenhouse_id = :gid AND date >= :f::date AND date <= :t::date "
                        "ORDER BY date LIMIT 100000"
                    ),
                    params,
                ).mappings().all()
                gt_data = [dict(r) for r in gt_rows]
                total_rows += len(gt_data)
                zf.writestr("ground_truth.parquet", _to_parquet_bytes(gt_data))

            # Annotations
            if payload.include_annotations:
                ann_rows = db.execute(
                    text(
                        "SELECT id, greenhouse_id, plant_id, sensor_id, start_time, end_time, label_key, label_value, "
                        "confidence, notes, status, created_at "
                        "FROM annotation WHERE greenhouse_id = :gid AND start_time >= :f AND start_time < :t "
                        "ORDER BY start_time LIMIT 100000"
                    ),
                    params,
                ).mappings().all()
                ann_data = [dict(r) for r in ann_rows]
                total_rows += len(ann_data)
                zf.writestr("annotations.parquet", _to_parquet_bytes(ann_data))

            # Schema metadata
            schema_info = {
                "export_id": str(job.id),
                "greenhouse_id": str(gh_id),
                "from": payload.from_time.isoformat(),
                "to": payload.to_time.isoformat(),
                "include_env": payload.include_env,
                "include_events": payload.include_events,
                "include_ground_truth": payload.include_ground_truth,
                "include_annotations": payload.include_annotations,
                "total_rows": total_rows,
            }
            zf.writestr("schema.json", json.dumps(schema_info, indent=2))
            zf.writestr("params.json", json.dumps(payload.model_dump(mode="json"), indent=2, default=str))

        archive_bytes = archive_buf.getvalue()

        # Upload to S3
        storage_key = f"exports/{gh_id}/{job.id}.zip"
        client = get_s3_client()
        client.put_object(
            Bucket=settings.s3_bucket,
            Key=storage_key,
            Body=archive_bytes,
            ContentType="application/zip",
        )

        job.status = "completed"
        job.storage_key = storage_key
        job.row_count = total_rows
        db.commit()

        return ExportResponse(export_id=job.id)

    except Exception as e:
        logger.exception("Export failed")
        job.status = "failed"
        job.error = {"error": str(e)}
        db.commit()
        raise HTTPException(status_code=500, detail="Export failed")


@router.get("/{export_id}/status", response_model=ExportStatusResponse)
def export_status(
    export_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    job = db.query(ExportJob).filter(ExportJob.id == export_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Export not found")
    resolve_greenhouse_id(user, job.greenhouse_id)

    return ExportStatusResponse(
        export_id=job.id,
        status=job.status,
        storage_key=job.storage_key,
        row_count=job.row_count,
        error=job.error,
        created_at=job.created_at,
    )


@router.get("/{export_id}/download")
def download_export(
    export_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    job = db.query(ExportJob).filter(ExportJob.id == export_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Export not found")
    resolve_greenhouse_id(user, job.greenhouse_id)

    if job.status != "completed" or not job.storage_key:
        raise HTTPException(status_code=400, detail="Export not ready or failed")

    client = get_s3_client()
    try:
        obj = client.get_object(Bucket=settings.s3_bucket, Key=job.storage_key)
        return StreamingResponse(
            obj["Body"],
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="export_{export_id}.zip"'},
        )
    except Exception:
        raise HTTPException(status_code=500, detail="Download failed")
