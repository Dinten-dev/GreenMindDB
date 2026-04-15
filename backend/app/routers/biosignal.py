"""Bio-signal ingestion and retrieval API."""

import json
import os
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.biosignal import BioAggregate, BioSession
from app.models.master import Gateway, Sensor
from app.models.timeseries import SensorReading
from app.routers.ws import manager
from app.services.wav_service import export_wav_from_session

router = APIRouter(prefix="/biosignal", tags=["biosignal"])

# Storage directory for raw high-frequency data
DATA_DIR = os.getenv("DATA_DIR", "/tmp/greenmind_data")  # noqa: S108
RAW_DATA_DIR = os.path.join(DATA_DIR, "raw_biosignals")
os.makedirs(RAW_DATA_DIR, exist_ok=True)


class BioIngestPayload(BaseModel):
    mac_address: str
    sample_rate: int
    hardware: str
    columns: list[str]
    readings: list[list[float | int]]  # [out_mv, lp, lm, flags]
    gateway_serial: str | None = None  # Injected by gateway proxy


def _resolve_sensor(db: Session, mac_address: str, gateway_serial: str | None) -> Sensor | None:
    """Find or auto-register a sensor for the given MAC, linking it to the gateway."""
    sensor = db.query(Sensor).filter(Sensor.mac_address == mac_address).first()
    if sensor:
        return sensor

    # No sensor with this MAC — try to find the gateway by hardware_id
    gateway = None
    if gateway_serial:
        gateway = db.query(Gateway).filter(Gateway.hardware_id == gateway_serial).first()

    if not gateway:
        return None

    # Auto-register the sensor under this gateway
    sensor = Sensor(
        gateway_id=gateway.id,
        mac_address=mac_address,
        name=f"AD8232-{mac_address[-5:].replace(':', '')}",
        sensor_type="bio_signal",
        status="online",
        last_seen=datetime.now(UTC),
    )
    db.add(sensor)
    db.flush()
    return sensor


@router.post("/ingest", status_code=201)
async def ingest_biosignal(payload: BioIngestPayload, db: Session = Depends(get_db)):
    """Ingest a chunk of high-frequency bio-signals (e.g. 1 second at 380Hz)."""
    now = datetime.now(UTC)

    # 1. Resolve sensor and gateway via the existing master tables
    sensor = _resolve_sensor(db, payload.mac_address, payload.gateway_serial)
    gateway_id = sensor.gateway_id if sensor else None

    # 2. Find or Create Active BioSession
    session = (
        db.query(BioSession)
        .filter(BioSession.sensor_mac == payload.mac_address, BioSession.end_time.is_(None))
        .order_by(BioSession.start_time.desc())
        .first()
    )

    if not session:
        session = BioSession(
            sensor_mac=payload.mac_address,
            gateway_id=gateway_id,
            hardware_model=payload.hardware,
            sample_rate_hz=payload.sample_rate,
            raw_storage_key=f"{uuid.uuid4()}.jsonl",
        )
        db.add(session)
        db.commit()
        db.refresh(session)

    # 3. Append Raw Data to JSONL
    filepath = os.path.join(RAW_DATA_DIR, session.raw_storage_key)
    with open(filepath, "a") as f:
        f.write(json.dumps({"timestamp": now.isoformat(), "readings": payload.readings}) + "\n")

    # 4. Calculate Aggregates
    if not payload.readings:
        return {"status": "ok", "session_id": str(session.id)}

    mvs = [r[0] for r in payload.readings]
    invalid_count = sum(1 for r in payload.readings if r[3] != 0)
    total_count = len(mvs)
    mean_mv = sum(mvs) / total_count

    agg = BioAggregate(
        session_id=session.id,
        timestamp=now,
        mean_mv=mean_mv,
        min_mv=min(mvs),
        max_mv=max(mvs),
        samples_total=total_count,
        samples_invalid=invalid_count,
    )
    db.add(agg)

    # Update session tally
    session.total_samples += total_count
    session.invalid_samples += invalid_count

    # 5. Write 1-second aggregate to sensor_reading for dashboard integration
    if sensor:
        sensor.last_seen = now
        sensor.status = "online"
        db.add(
            SensorReading(
                timestamp=now,
                sensor_id=sensor.id,
                kind="bio_signal",
                value=round(mean_mv, 2),
                unit="mV",
            )
        )

    db.commit()

    # 6. Broadcast live data to Frontend
    frontend_readings = [{"t": now.isoformat(), "v": mv} for mv in mvs]
    room_id = str(gateway_id) if gateway_id else "biosignal"
    await manager.broadcast_to_zone(
        {"event": "bio_stream", "mac": payload.mac_address, "data": frontend_readings},
        room_id,
    )

    return {"status": "ok", "session_id": str(session.id)}


@router.post("/sessions/{session_id}/export-wav")
def trigger_wav_export(
    session_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)
):
    """Triggers the WAV export for a session."""
    session = db.query(BioSession).filter(BioSession.id == session_id).first()
    if not session:
        raise HTTPException(404, "Session not found")

    if not session.raw_storage_key:
        raise HTTPException(400, "No raw data available for this session")

    # Mark session as ended if not already
    if not session.end_time:
        session.end_time = datetime.now(UTC)
        db.commit()

    raw_path = os.path.join(RAW_DATA_DIR, session.raw_storage_key)
    background_tasks.add_task(
        export_wav_from_session, str(session.id), raw_path, session.sample_rate_hz
    )

    return {"status": "processing", "message": "WAV export started in background."}


@router.get("/sessions/{session_id}")
def get_session(session_id: str, db: Session = Depends(get_db)):
    """Fetch session metadata and health stats."""
    session = db.query(BioSession).filter(BioSession.id == session_id).first()
    if not session:
        raise HTTPException(404, "Session not found")

    return {
        "id": str(session.id),
        "mac": session.sensor_mac,
        "total_samples": session.total_samples,
        "invalid_samples": session.invalid_samples,
        "duration_seconds": (
            (session.end_time or datetime.now(UTC)) - session.start_time
        ).total_seconds(),
        "wav_ready": bool(session.wav_storage_key),
    }


@router.get("/sessions/{session_id}/signal")
def get_session_signal(session_id: str, db: Session = Depends(get_db)):
    """Return the aggregated signal for frontend charting (only output_voltage)."""
    aggs = (
        db.query(BioAggregate)
        .filter(BioAggregate.session_id == session_id)
        .order_by(BioAggregate.timestamp.asc())
        .all()
    )
    # Provide the 1-second averages
    return {
        "data": [
            {
                "timestamp": agg.timestamp.isoformat(),
                "value": agg.mean_mv,
                "invalid": agg.samples_invalid > 0,
            }
            for agg in aggs
        ]
    }
