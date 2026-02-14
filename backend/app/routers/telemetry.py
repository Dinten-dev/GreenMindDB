from fastapi import APIRouter, Depends, HTTPException, Query, Header, Request
from fastapi.responses import StreamingResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session
from sqlalchemy import text, select, func, desc
from typing import List, Optional, Any
from pydantic import BaseModel
from datetime import datetime, timedelta
import uuid
import io
import csv

from app.database import get_db
from app.auth import get_current_user, verify_device_api_key
from app.models.telemetry import TelemetryChannel, TelemetryMeasurement, Device
from app.models import Metric, Species

router = APIRouter(
    prefix="/species/{species_id}/live",
    tags=["telemetry"]
)

ingest_router = APIRouter(tags=["ingest"])

# Rate limiter for ingest endpoint
limiter = Limiter(key_func=get_remote_address)

# --- Pydantic Models ---

class IngestSample(BaseModel):
    timestamp: Optional[datetime] = None
    metric_key: str
    value: float
    species_name: str 

class IngestPayload(BaseModel):
    device_id: Optional[str] = None
    samples: List[IngestSample]

class MetricPoint(BaseModel):
    timestamp: datetime
    value: float

class TimeseriesResponse(BaseModel):
    metric_key: str
    unit: str
    points: List[List[Any]] # [timestamp, value]

class LatestValue(BaseModel):
    metric_key: str
    value: Optional[float] = None
    unit: str
    timestamp: Optional[datetime] = None

class LatestResponse(BaseModel):
    species_id: int
    latest: List[LatestValue]

# Canonical metrics - always returned even if no data
CANONICAL_METRICS = [
    {"key": "air_temperature_c", "label": "Temperature", "unit": "°C"},
    {"key": "rel_humidity_pct", "label": "Humidity", "unit": "%"},
    {"key": "light_ppfd_umol_m2_s", "label": "Light (PPFD)", "unit": "µmol/m²/s"},
    {"key": "soil_moisture_vwc_pct", "label": "Soil Moisture", "unit": "%"},
    {"key": "soil_ph", "label": "Soil pH", "unit": "pH"},
    {"key": "bioelectric_voltage_mv", "label": "Bioelectric", "unit": "mV"},
]

# --- Ingest Endpoint ---

@ingest_router.post("/ingest", status_code=201)
@limiter.limit("100/minute")
def ingest_data(
    request: Request,
    payload: IngestPayload,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """
    Robust Ingest:
    - Resolves species_name -> species_id
    - Resolves metric_key -> metric_id
    - Finds or Creates Channel (UNIQUE per species_id, metric_id)
    - Inserts Measurement
    """
    if not authorization or not authorization.startswith("Bearer "):
         raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    
    api_key = authorization.split(" ")[1]
    
    # Verify Device (Optimized: Could cache)
    # We do a join here or raw query to be fast? ORM is fine for now.
    stmt = select(Device).where(Device.api_key_hash != None).where(Device.is_active == True)
    # We can't verify hash in SQL easily without extension, so fetch candidate by ID if provided, or scan?
    # Actually device_id should be in payload or we assume key is unique enough? 
    # Current verify_device_api_key takes key and hash. We need the device first.
    # The payload has device_id usually.
    
    device_id = payload.device_id
    device = None
    if device_id:
        device = db.execute(select(Device).where(Device.id == device_id)).scalar_one_or_none()
        
    if not device:
         # Try to find by iterating? No, that's slow.
         # Requirement said: "Header: Authorization: Bearer <device_api_key>"
         # And "IngestPayload... device_id".
         # So we expect device_id in payload.
         raise HTTPException(status_code=401, detail="Device ID required")

    if not verify_device_api_key(api_key, device.api_key_hash):
        raise HTTPException(status_code=401, detail="Invalid credential")

    # Cache Resolution
    # species_map: name -> id
    # metric_map: key -> (id, unit)
    
    species_map = {}
    metric_map = {}
    
    measurements_to_insert = []
    
    for sample in payload.samples:
        # A. Resolve Species
        s_name = sample.species_name.strip()
        if s_name not in species_map:
            res = db.execute(text("SELECT id FROM species WHERE lower(common_name) = lower(:name)"), {"name": s_name}).fetchone()
            if not res:
                # Try latin
                res = db.execute(text("SELECT id FROM species WHERE lower(latin_name) = lower(:name)"), {"name": s_name}).fetchone()
            
            if res:
                 species_map[s_name] = res.id
            else:
                 continue # Skip unknown species
            
        species_id = species_map[s_name]
        
        # B. Resolve Metric
        m_key = sample.metric_key
        if m_key not in metric_map:
            res = db.execute(text("SELECT id, unit FROM metric WHERE key = :key"), {"key": m_key}).fetchone()
            if res:
                metric_map[m_key] = res
            else:
                # "bioelectric_voltage_mv" support:
                # If migration added it, good. If not, and we must support it, we assume DB has it.
                # If DB miss, we skip.
                continue
                
        metric_info = metric_map[m_key]
        metric_id = metric_info.id
        unit = metric_info.unit
        
        # C. Get/Create Channel
        # UNIQUE(species_id, metric_id)
        # We can implement a local cache for this batch too
        
        channel_id = None
        # Try fetch
        chan_res = db.execute(
            text("SELECT id FROM telemetry_channel WHERE species_id = :sid AND metric_id = :mid"),
            {"sid": species_id, "mid": metric_id}
        ).fetchone()
        
        if chan_res:
            channel_id = chan_res.id
        else:
            # Create
            # channel_key is still required by schema but now constraint is on (species, metric).
            # We construct a key that is ostensibly unique. {species_id}_{metric_id}
            # Or use random UUID.
            new_id = uuid.uuid4()
            try:
                # Key must be unique too if the old constraint exists? 
                # Migration 004 removed channel_key unique constraint? 
                # Yes, "op.drop_constraint('telemetry_channel_channel_key_key'..."
                
                db.execute(
                    text("""
                        INSERT INTO telemetry_channel (id, species_id, metric_id, device_id, channel_key, unit, created_at)
                        VALUES (:id, :sid, :mid, :did, :key, :unit, now())
                        ON CONFLICT (species_id, metric_id) DO UPDATE SET device_id = EXCLUDED.device_id RETURNING id
                    """),
                    {
                        "id": new_id,
                        "sid": species_id,
                        "mid": metric_id,
                        "did": device.id,
                        # channel_key: make it descriptive but it doesn't have to be unique anymore by DB rules (if we dropped it)
                        # but keeping it clean is nice.
                        "key": f"{s_name}_{m_key}", 
                        "unit": unit
                    }
                )
                # We need to commit inner transaction or flush?
                # SQL Alchemy `db.execute` on session usually requires commit for visibility in other sessions,
                # but here valid for this transaction.
                # ON CONFLICT RETURNING id handles the race.
                
                # Fetch the ID again to be sure (since RETURNING might return the new one or the updated one)
                # Actually RETURNING on update works.
            except Exception as e:
                # Fallback
                pass
            
            # Re-fetch to be safe/consistent
            chan_res = db.execute(
                text("SELECT id FROM telemetry_channel WHERE species_id = :sid AND metric_id = :mid"),
                {"sid": species_id, "mid": metric_id}
            ).fetchone()
            if chan_res:
                channel_id = chan_res.id

        if channel_id:
            ts = sample.timestamp or datetime.utcnow()
            measurements_to_insert.append({
                "time": ts,
                "channel_id": channel_id,
                "value": sample.value,
                "quality": 0 
            })
            
    # Bulk Insert
    if measurements_to_insert:
        db.execute(
            text("""
                INSERT INTO telemetry_measurement (time, channel_id, value, quality)
                VALUES (:time, :channel_id, :value, :quality)
            """),
            measurements_to_insert
        )
        db.commit()
            
    return {"status": "ok", "processed": len(measurements_to_insert)}


# --- Live API Endpoints ---

@router.get("/latest", response_model=LatestResponse)
def get_latest_live_data(
    species_id: int,
    db: Session = Depends(get_db)
):
    """
    Return latest values for ALL canonical metrics.
    If a metric has no data yet, value and timestamp are null.
    """
    # 1. Build a map of existing channels for this species
    stmt = select(TelemetryChannel.id, TelemetryChannel.unit, Metric.key.label("metric_key")).join(Metric).where(TelemetryChannel.species_id == species_id)
    channels = db.execute(stmt).fetchall()
    channel_map = {ch.metric_key: ch for ch in channels}
    
    results = []
    
    # 2. Iterate over canonical metrics (always return all)
    for cm in CANONICAL_METRICS:
        metric_key = cm["key"]
        unit = cm["unit"]
        
        ch = channel_map.get(metric_key)
        
        if ch:
            # Get last value from channel
            val_res = db.execute(
                text("SELECT time, value FROM telemetry_measurement WHERE channel_id = :cid ORDER BY time DESC LIMIT 1"),
                {"cid": ch.id}
            ).fetchone()
            
            if val_res:
                results.append(LatestValue(
                    metric_key=metric_key,
                    value=val_res.value,
                    unit=ch.unit,
                    timestamp=val_res.time
                ))
            else:
                # Channel exists but no measurements yet
                results.append(LatestValue(
                    metric_key=metric_key,
                    value=None,
                    unit=ch.unit,
                    timestamp=None
                ))
        else:
            # No channel for this metric yet - return null placeholder
            results.append(LatestValue(
                metric_key=metric_key,
                value=None,
                unit=unit,
                timestamp=None
            ))
            
    return LatestResponse(species_id=species_id, latest=results)


@router.get("/timeseries", response_model=TimeseriesResponse)
def get_timeseries(
    species_id: int,
    metric_key: str,
    range: str = "24h", # 1h, 6h, 24h, 72h, 7d
    db: Session = Depends(get_db)
):
    # 1. Resolve Metric
    m_res = db.execute(text("SELECT id FROM metric WHERE key = :key"), {"key": metric_key}).fetchone()
    if not m_res:
        raise HTTPException(status_code=404, detail="Metric not found")
    metric_id = m_res.id
    
    # 2. Get Channel
    c_res = db.execute(
        text("SELECT id, unit FROM telemetry_channel WHERE species_id = :sid AND metric_id = :mid"),
        {"sid": species_id, "mid": metric_id}
    ).fetchone()
    
    if not c_res:
        return TimeseriesResponse(metric_key=metric_key, unit="", points=[])
        
    channel_id = c_res.id
    unit = c_res.unit
    
    # 3. Determine Bucket Interval
    interval_map = {
        "1h": "1 minute",
        "6h": "5 minutes",
        "24h": "15 minutes",
        "72h": "1 hour",
        "7d": "2 hours"
    }
    interval = interval_map.get(range, "15 minutes")
    
    pg_range_map = {
        "1h": "1 hour",
        "6h": "6 hours",
        "24h": "24 hours",
        "72h": "72 hours",
        "7d": "7 days"
    }
    pg_range = pg_range_map.get(range, "24 hours")
    
    # 4. Timescale time_bucket query
    # Using raw SQL for time_bucket which is a TimescaleDB function
    query = text(f"""
        SELECT 
            time_bucket(:bucket, time) AS bucket,
            avg(value) as avg_val
        FROM telemetry_measurement
        WHERE channel_id = :cid
          AND time > now() - INTERVAL '{pg_range}'
        GROUP BY bucket
        ORDER BY bucket ASC
    """)
    
    rows = db.execute(query, {"bucket": interval, "cid": channel_id}).fetchall()
    
    points = [[r.bucket, r.avg_val] for r in rows]
    
    return TimeseriesResponse(
        metric_key=metric_key,
        unit=unit,
        points=points
    )

@router.get("/download")
def download_csv(
    species_id: int,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    db: Session = Depends(get_db)
):
    if not from_date:
        from_date = datetime.utcnow() - timedelta(hours=24)
    if not to_date:
        to_date = datetime.utcnow()

    # Get channels
    stmt = select(TelemetryChannel.id, TelemetryChannel.unit, Metric.key.label("metric_key")).join(Metric).where(TelemetryChannel.species_id == species_id)
    channels = db.execute(stmt).fetchall()
    
    if not channels:
          return StreamingResponse(io.StringIO("No data"), media_type="text/csv")
          
    channel_map = {c.id: c for c in channels}
    channel_ids = [c.id for c in channels]
    
    # Query
    stmt = select(TelemetryMeasurement).where(
        TelemetryMeasurement.channel_id.in_(channel_ids),
        TelemetryMeasurement.time >= from_date,
        TelemetryMeasurement.time <= to_date
    ).order_by(TelemetryMeasurement.time)
    
    rows = db.execute(stmt).scalars().all()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["timestamp", "metric_key", "value", "unit"])
    
    for r in rows:
        ch = channel_map.get(r.channel_id)
        if ch:
            writer.writerow([
                r.time.isoformat(),
                ch.metric_key,
                r.value,
                ch.unit
            ])
        
    output.seek(0)
    # Using streaming response wrapper if available or return raw
    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=species_{species_id}_data.csv"}
    )


@router.get("/download/wav")
def download_bioelectric_wav(
    species_id: int,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    sample_rate: int = 44100,
    db: Session = Depends(get_db)
):
    """
    Download bioelectric voltage data as WAV audio file.
    Converts mV values to 16-bit PCM audio.
    """
    import wave
    import struct
    
    if not from_date:
        from_date = datetime.utcnow() - timedelta(hours=24)
    if not to_date:
        to_date = datetime.utcnow()
    
    # Get bioelectric channel for this species
    bioelectric_metric = db.execute(
        text("SELECT id FROM metric WHERE key = 'bioelectric_voltage_mv'")
    ).fetchone()
    
    if not bioelectric_metric:
        return StreamingResponse(
            io.BytesIO(b""),
            media_type="audio/wav",
            headers={"Content-Disposition": f"attachment; filename=no_bioelectric_data.wav"}
        )
    
    channel = db.execute(
        text("SELECT id FROM telemetry_channel WHERE species_id = :sid AND metric_id = :mid"),
        {"sid": species_id, "mid": bioelectric_metric.id}
    ).fetchone()
    
    if not channel:
        return StreamingResponse(
            io.BytesIO(b""),
            media_type="audio/wav",
            headers={"Content-Disposition": f"attachment; filename=no_bioelectric_data.wav"}
        )
    
    # Query raw measurements
    rows = db.execute(
        text("""
            SELECT time, value FROM telemetry_measurement 
            WHERE channel_id = :cid AND time >= :from_date AND time <= :to_date
            ORDER BY time ASC
        """),
        {"cid": channel.id, "from_date": from_date, "to_date": to_date}
    ).fetchall()
    
    if not rows:
        # Return empty WAV
        output = io.BytesIO()
        with wave.open(output, 'wb') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)  # 16-bit
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(b'')
        output.seek(0)
        return StreamingResponse(
            output,
            media_type="audio/wav",
            headers={"Content-Disposition": f"attachment; filename=species_{species_id}_bioelectric.wav"}
        )
    
    # Convert mV values to 16-bit PCM
    # Normalize to [-32768, 32767] range
    values = [r.value for r in rows]
    if values:
        max_val = max(abs(v) for v in values) or 1.0
        # Scale to 16-bit range with headroom
        scale = 32000 / max_val
        samples = [int(v * scale) for v in values]
        # Clamp values
        samples = [max(-32768, min(32767, s)) for s in samples]
    else:
        samples = []
    
    # Create WAV file
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav_file:
        wav_file.setnchannels(1)  # Mono
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(sample_rate)
        # Pack samples as signed 16-bit integers
        packed = struct.pack('<' + 'h' * len(samples), *samples)
        wav_file.writeframes(packed)
    
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="audio/wav",
        headers={"Content-Disposition": f"attachment; filename=species_{species_id}_bioelectric.wav"}
    )
