"""WAV file management endpoints — upload from gateways, list, download."""

import logging
import uuid
from datetime import datetime
from io import BytesIO

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.auth import verify_password
from app.database import get_db
from app.models.master import Gateway, Sensor
from app.models.wav_file import WavFile
from app.services import wav_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/wav", tags=["wav"])


@router.post("/upload", status_code=201)
async def upload_wav(
    file: UploadFile = File(...),
    sensor_mac: str = Form(...),
    gateway_serial: str = Form(...),
    sample_rate: int = Form(380),
    started_at: str = Form(...),
    ended_at: str = Form(...),
    db: Session = Depends(get_db),
    x_api_key: str | None = Header(None),
):
    """Receive a WAV file from a gateway and store in MinIO.

    Authenticated via X-Api-Key (same as /ingest).
    """
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-Api-Key header")

    # Authenticate gateway
    gateway = db.query(Gateway).filter(Gateway.hardware_id == gateway_serial).first()
    if not gateway or not gateway.api_key_hash:
        raise HTTPException(status_code=403, detail="Unknown or unconfigured gateway")
    if not verify_password(x_api_key, gateway.api_key_hash):
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Parse timestamps
    try:
        start_dt = datetime.fromisoformat(started_at)
        end_dt = datetime.fromisoformat(ended_at)
    except ValueError as e:
        raise HTTPException(
            status_code=400, detail="Invalid timestamp format (ISO 8601 required)"
        ) from e

    # Read file
    file_data = await file.read()
    file_size = len(file_data)
    file_io = BytesIO(file_data)

    # Extract WAV metadata for validation
    try:
        meta = wav_service.extract_wav_metadata(file_io)
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid WAV file") from e

    # Upload to MinIO
    s3_key = wav_service.upload_wav(
        file_data=file_io,
        sensor_mac=sensor_mac,
        started_at=start_dt,
        file_size=file_size,
    )

    # Find or identify sensor
    sensor = db.query(Sensor).filter(Sensor.mac_address == sensor_mac).first()
    sensor_id = sensor.id if sensor else uuid.uuid4()

    # Create DB record
    wav_record = WavFile(
        sensor_id=sensor_id,
        gateway_id=gateway.id,
        sensor_mac=sensor_mac,
        s3_key=s3_key,
        sample_rate=meta.get("sample_rate", sample_rate),
        duration_seconds=meta.get("duration_seconds", 0.0),
        file_size_bytes=file_size,
        started_at=start_dt,
        ended_at=end_dt,
    )
    db.add(wav_record)
    db.commit()
    db.refresh(wav_record)

    logger.info(
        "WAV uploaded: sensor=%s s3=%s duration=%.1fs",
        sensor_mac,
        s3_key,
        wav_record.duration_seconds,
    )

    return {
        "status": "ok",
        "wav_id": str(wav_record.id),
        "s3_key": s3_key,
        "duration_seconds": wav_record.duration_seconds,
    }


@router.get("/files")
def list_wav_files(
    sensor_id: str | None = None,
    sensor_mac: str | None = None,
    from_dt: str | None = None,
    to_dt: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    """List available WAV files with optional filters."""
    query = db.query(WavFile)

    if sensor_id:
        query = query.filter(WavFile.sensor_id == sensor_id)
    if sensor_mac:
        query = query.filter(WavFile.sensor_mac == sensor_mac)
    if from_dt:
        try:
            query = query.filter(WavFile.started_at >= datetime.fromisoformat(from_dt))
        except ValueError as e:
            raise HTTPException(status_code=400, detail="Invalid from_dt format") from e
    if to_dt:
        try:
            query = query.filter(WavFile.ended_at <= datetime.fromisoformat(to_dt))
        except ValueError as e:
            raise HTTPException(status_code=400, detail="Invalid to_dt format") from e

    files = query.order_by(desc(WavFile.started_at)).limit(min(limit, 500)).all()

    return [
        {
            "id": str(f.id),
            "sensor_mac": f.sensor_mac,
            "sensor_id": str(f.sensor_id),
            "sample_rate": f.sample_rate,
            "duration_seconds": f.duration_seconds,
            "file_size_bytes": f.file_size_bytes,
            "started_at": f.started_at.isoformat(),
            "ended_at": f.ended_at.isoformat(),
            "created_at": f.created_at.isoformat(),
        }
        for f in files
    ]


@router.get("/download/{wav_id}")
def download_wav(
    wav_id: str,
    db: Session = Depends(get_db),
):
    """Get a presigned download URL for a WAV file."""
    wav_file = db.query(WavFile).filter(WavFile.id == wav_id).first()
    if not wav_file:
        raise HTTPException(status_code=404, detail="WAV file not found")

    url = wav_service.generate_presigned_url(wav_file.s3_key)

    return {
        "download_url": url,
        "s3_key": wav_file.s3_key,
        "sensor_mac": wav_file.sensor_mac,
        "duration_seconds": wav_file.duration_seconds,
        "started_at": wav_file.started_at.isoformat(),
    }
