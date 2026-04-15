"""Service layer for WAV file storage in MinIO/S3."""

import logging
import wave
from datetime import datetime
from typing import BinaryIO

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from app.config import settings

logger = logging.getLogger(__name__)

# Lazy-initialized S3 client
_s3_client = None
_WAV_BUCKET = "greenmind-raw"


def _get_s3_client():
    """Get or create the S3/MinIO client."""
    global _s3_client
    if _s3_client is not None:
        return _s3_client

    _s3_client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        region_name=settings.s3_region,
        config=BotoConfig(signature_version="s3v4"),
    )

    # Ensure bucket exists
    try:
        _s3_client.head_bucket(Bucket=_WAV_BUCKET)
    except ClientError:
        logger.info("Creating S3 bucket: %s", _WAV_BUCKET)
        _s3_client.create_bucket(Bucket=_WAV_BUCKET)

    return _s3_client


def upload_wav(
    file_data: BinaryIO,
    sensor_mac: str,
    started_at: datetime,
    file_size: int,
) -> str:
    """Upload a WAV file to MinIO and return the S3 key.

    Key format: wav/{sensor_mac}/{YYYYMMDD}/{HHmmss}.wav
    """
    date_str = started_at.strftime("%Y%m%d")
    time_str = started_at.strftime("%H%M%S")
    mac_clean = sensor_mac.replace(":", "").upper()
    s3_key = f"wav/{mac_clean}/{date_str}/{time_str}.wav"

    client = _get_s3_client()
    client.upload_fileobj(
        file_data,
        _WAV_BUCKET,
        s3_key,
        ExtraArgs={"ContentType": "audio/wav"},
    )

    logger.info("Uploaded WAV to s3://%s/%s (%d bytes)", _WAV_BUCKET, s3_key, file_size)
    return s3_key


def generate_presigned_url(s3_key: str, expires_in: int = 3600) -> str:
    """Generate a presigned download URL for a WAV file."""
    client = _get_s3_client()
    url = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": _WAV_BUCKET, "Key": s3_key},
        ExpiresIn=expires_in,
    )
    return url


def extract_wav_metadata(file_data: BinaryIO) -> dict:
    """Extract metadata from a WAV file (sample rate, duration, size).

    Returns dict with sample_rate, duration_seconds, n_samples.
    """
    file_data.seek(0)
    try:
        with wave.open(file_data, "rb") as wf:
            sample_rate = wf.getframerate()
            n_frames = wf.getnframes()
            duration = n_frames / sample_rate if sample_rate > 0 else 0.0
            return {
                "sample_rate": sample_rate,
                "duration_seconds": round(duration, 2),
                "n_samples": n_frames,
            }
    finally:
        file_data.seek(0)


def delete_wav(s3_key: str) -> None:
    """Delete a WAV file from MinIO."""
    client = _get_s3_client()
    client.delete_object(Bucket=_WAV_BUCKET, Key=s3_key)
    logger.info("Deleted WAV s3://%s/%s", _WAV_BUCKET, s3_key)


def export_wav_from_session(session_id: str, raw_path: str, sample_rate: int) -> None:
    """Read a raw JSONL session log, convert the signal to a clean WAV, and upload it."""
    import io
    import json
    import os
    import struct

    from app.database import SessionLocal
    from app.models.biosignal import BioSession

    if not os.path.exists(raw_path):
        logger.error(f"Cannot export session {session_id}, raw file not found at {raw_path}")
        return

    frames = []

    # Process the raw signal
    with open(raw_path) as f:
        for line in f:
            if not line.strip():
                continue
            try:
                batch = json.loads(line)
                readings = batch["readings"]
                for r in readings:
                    mv_val = r[0]
                    flags = r[3]

                    # Clean generation: if invalid (flag > 0), set to 0 PCM (1.65V baseline equivalent)
                    if flags != 0:
                        pcm_val = 0
                    else:
                        # AD8232 limits: 0 .. 3300mV. Centered at 1650mV.
                        # Scale perfectly to 16 bit PCM (-32768 to +32767)
                        normalized = (mv_val - 1650.0) / 1650.0
                        # Clamp
                        normalized = max(min(normalized, 1.0), -1.0)
                        pcm_val = int(normalized * 32767)

                    frames.append(struct.pack("<h", pcm_val))
            except Exception as e:
                logger.error(f"Export parsing error in {raw_path}: {e}")

    # Generate WAV in-memory
    wav_io = io.BytesIO()
    with wave.open(wav_io, "wb") as wf:
        wf.setnchannels(1)  # Mono
        wf.setsampwidth(2)  # 16-bit PCM
        wf.setframerate(sample_rate)
        wf.writeframes(b"".join(frames))

    wav_size = wav_io.tell()
    wav_io.seek(0)

    # Store back to DB
    with SessionLocal() as db:
        session = db.query(BioSession).filter(BioSession.id == session_id).first()
        if session:
            # We use upload_wav logic to upload to MinIO
            s3_key = upload_wav(
                wav_io,
                sensor_mac=session.sensor_mac,
                started_at=session.start_time,
                file_size=wav_size,
            )
            session.wav_storage_key = s3_key
            db.commit()
            logger.info(f"Session {session_id} WAV exported successfully to {s3_key}")
