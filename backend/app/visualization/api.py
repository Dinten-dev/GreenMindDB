"""Authenticated read sidecar; it exposes no ingestion or deletion endpoints."""

import csv
import hashlib
import io
import math
import os
import re
import uuid
import wave
import zipfile
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import Response
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import settings
from app.database import get_db
from app.models.master import Gateway, Sensor, Zone
from app.models.user import User
from app.rate_limit import limiter
from app.zone_access import zone_access_filter

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
read_engine = create_engine(
    os.environ.get("DATABASE_URL", "postgresql+psycopg2://localhost/plantdb"),
    pool_size=3,
    max_overflow=0,
    pool_timeout=5,
    pool_pre_ping=True,
    connect_args={"options": "-c statement_timeout=8000 -c lock_timeout=150 -c work_mem=8192"},
)


def read_db():
    with Session(read_engine) as session:
        yield session


app.dependency_overrides[get_db] = read_db

RANGES = {
    "5m": timedelta(minutes=5),
    "1h": timedelta(hours=1),
    "24h": timedelta(days=1),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}
RESOLUTIONS = {"raw": 1, "1m": 60, "5m": 300, "10m": 600, "1h": 3600, "1d": 86400}


@app.middleware("http")
async def private_response(request, call_next):
    result = await call_next(request)
    result.headers["Cache-Control"] = "private, no-store"
    result.headers["X-Content-Type-Options"] = "nosniff"
    return result


def authorize(db, user, sensor_id):
    if not user.organization_id:
        raise HTTPException(403, "No organization")
    sensor = (
        db.query(Sensor.id)
        .join(Gateway, Gateway.id == Sensor.gateway_id)
        .join(Zone, Zone.id == Gateway.zone_id)
        .filter(Sensor.id == sensor_id, zone_access_filter(user))
        .first()
    )
    if not sensor:
        raise HTTPException(404, "Sensor not found")


def uncovered(start, end, ranges):
    cursor = start
    segments = []
    for left, right in sorted(ranges):
        if right <= cursor:
            continue
        if left > cursor:
            segments.append((cursor, min(left, end)))
        cursor = max(cursor, right)
        if cursor >= end:
            break
    if cursor < end:
        segments.append((cursor, end))
    return [(a, b) for a, b in segments if a < b]


def query_series(db, sensor_id, start, end, requested, limit=20000):
    db.execute(text("SET LOCAL statement_timeout='8s'"))
    db.execute(text("SET LOCAL lock_timeout='150ms'"))
    params = {"sid": sensor_id, "start": start, "end": end, "step": requested, "lim": limit}
    # A verified chunk's source rows are represented atomically by its rollups.
    # Late arrivals are incorporated by the next worker pass, never double counted.
    covered = db.execute(
        text(
            "SELECT range_start,range_end FROM visual_chunk WHERE status IN ('verified','compacted') AND range_start<:end AND range_end>:start"
        ),
        params,
    ).all()
    grouped = (
        db.execute(
            text("""SELECT kind,unit,to_timestamp(floor(extract(epoch FROM bucket)/greatest(seconds,:step))*greatest(seconds,:step)) AS stamp,
      greatest(seconds,:step) AS seconds,sum(n) AS n,sum(total) AS total,sum(total2) AS total2,min(minimum) AS minimum,max(maximum) AS maximum
      FROM visual_reading WHERE sensor_id=:sid AND bucket<:end AND bucket+seconds*interval '1 second'>:start
      GROUP BY kind,unit,stamp,greatest(seconds,:step) ORDER BY stamp LIMIT :lim"""),
            params,
        )
        .mappings()
        .all()
    )
    rows = [dict(row) for row in grouped]
    for left, right in uncovered(start, end, covered):
        part = params | {"start": left, "end": right}
        raw = (
            db.execute(
                text("""SELECT kind,unit,to_timestamp(floor(extract(epoch FROM timestamp)/:step)*:step) AS stamp,
          :step AS seconds,count(*) AS n,sum(value) AS total,sum(value*value) AS total2,min(value) AS minimum,max(value) AS maximum
          FROM sensor_reading WHERE sensor_id=:sid AND timestamp>=:start AND timestamp<:end
          GROUP BY kind,unit,stamp ORDER BY stamp LIMIT :lim"""),
                part,
            )
            .mappings()
            .all()
        )
        rows.extend(dict(row) for row in raw)
    if len(rows) >= limit:
        raise HTTPException(
            413, "Time window contains too many points; choose a coarser resolution"
        )
    combined = {}
    for row in rows:
        row["n"] = int(row["n"])
        key = (row["kind"], row["unit"], row["stamp"], row["seconds"])
        if key not in combined:
            combined[key] = row
        else:
            target = combined[key]
            for field in ("n", "total", "total2"):
                target[field] += row[field]
            target["minimum"] = min(target["minimum"], row["minimum"])
            target["maximum"] = max(target["maximum"], row["maximum"])
    waves = (
        db.execute(
            text("""SELECT to_timestamp(floor(extract(epoch FROM w.bucket)/greatest(w.seconds,:step))*greatest(w.seconds,:step)) AS stamp,
      greatest(w.seconds,:step) AS seconds,sum(n) AS n,sum(total) AS total,sum(total2) AS total2,
      min(minimum) AS minimum,max(maximum) AS maximum,sum(duration) AS duration
      FROM visual_wave w JOIN visual_wav v ON v.wav_id=w.wav_id
      WHERE w.sensor_id=:sid AND w.bucket<:end AND w.bucket+w.seconds*interval '1 second'>:start AND v.status='verified'
      GROUP BY stamp,greatest(w.seconds,:step)"""),
            params,
        )
        .mappings()
        .all()
    )
    wave_map = {(row["stamp"], row["seconds"]): row for row in waves}
    series = defaultdict(list)
    for row in sorted(combined.values(), key=lambda r: r["stamp"]):
        mean = row["total"] / row["n"]
        point = {
            "timestamp": row["stamp"].isoformat(),
            "value": round(mean, 4),
            "resolution_seconds": row["seconds"],
            "reading_count": row["n"],
            "minimum": None,
            "maximum": None,
            "rms": None,
            "standard_deviation": None,
            "coverage_ratio": None,
            "signal_source": "pending",
        }
        observed = (
            wave_map.get((row["stamp"], row["seconds"]))
            if row["kind"] in ("bio_signal", "bioelectric") and row["unit"] == "mV"
            else None
        )
        if observed:
            coverage = observed["duration"] / row["seconds"]
            if coverage <= 1.02:
                rms = math.sqrt(max(0, observed["total2"] / int(observed["n"])))
                wave_mean = observed["total"] / int(observed["n"])
                point.update(
                    minimum=round(observed["minimum"], 4),
                    maximum=round(observed["maximum"], 4),
                    rms=round(rms, 4),
                    standard_deviation=round(
                        math.sqrt(max(0, rms * rms - wave_mean * wave_mean)), 4
                    ),
                    coverage_ratio=min(1, coverage),
                    signal_source="wav",
                )
            else:
                point["signal_source"] = "overlap"
        elif row["kind"] not in ("bio_signal", "bioelectric"):
            point.update(
                minimum=row["minimum"],
                maximum=row["maximum"],
                rms=math.sqrt(max(0, row["total2"] / row["n"])),
                standard_deviation=math.sqrt(max(0, row["total2"] / row["n"] - mean * mean)),
                signal_source="readings",
            )
        series[(row["kind"], row["unit"])].append(point)
    return [
        {
            "sensor_id": str(sensor_id),
            "kind": kind,
            "unit": unit,
            "data": points,
            "aggregation": "24h:1s;7d:1m;older:10m",
            "original_signal_available": kind in ("bio_signal", "bioelectric"),
        }
        for (kind, unit), points in series.items()
    ]


@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1 FROM visual_reading LIMIT 1"))
    row = (
        db.execute(text("SELECT status,updated_at FROM visual_worker WHERE name='compactor'"))
        .mappings()
        .first()
    )
    return {
        "visualization": "healthy",
        "worker": dict(row) if row else None,
        "release_revision": os.environ.get("RELEASE_REVISION"),
        "release_id": os.environ.get("RELEASE_ID"),
    }


@app.get("/api/v1/sensors/{sensor_id}/data")
@app.get("/api/v1/visualization/sensors/{sensor_id}/data")
def data(
    sensor_id: uuid.UUID,
    range: str = Query("24h", pattern="^(5m|1h|24h|7d|30d)$"),
    resolution: str | None = Query(None, pattern="^(raw|1m|5m|10m|1h|1d)$"),
    date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    authorize(db, user, sensor_id)
    end = datetime.now(UTC)
    start = end - RANGES[range]
    if date:
        try:
            start = datetime.fromisoformat(date).replace(tzinfo=UTC)
            end = start + timedelta(days=1)
        except ValueError as exc:
            raise HTTPException(400, "Invalid date") from exc
    step = (
        RESOLUTIONS[resolution]
        if resolution
        else (
            1
            if end - start <= timedelta(hours=1)
            else 60
            if end - start <= timedelta(days=7)
            else 600
        )
    )
    return query_series(db, sensor_id, start, end, step)


def safe_text(value):
    result = str(value).replace("\n", " ").replace("\r", " ").strip()
    return "'" + result if result.startswith(("=", "+", "-", "@")) else result


@app.get("/api/v1/sensors/{sensor_id}/export")
@limiter.limit("3/minute")
def export(
    request: Request,
    sensor_id: uuid.UUID,
    range: str = Query("24h", pattern="^(1h|24h|7d|30d|all)$"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    authorize(db, user, sensor_id)
    end = datetime.now(UTC)
    start = end - (timedelta(days=3650) if range == "all" else RANGES[range])
    step = 1 if range == "1h" else 60 if range in ("24h", "7d") else 600
    records = query_series(db, sensor_id, start, end, step, settings.sensor_export_max_rows)
    if not records:
        raise HTTPException(404, "No data available for export")
    if len(records) > settings.sensor_export_max_kinds:
        raise HTTPException(413, "Export exceeds measurement kind limit")
    metadata = (
        db.execute(
            text("""SELECT to_jsonb(s) AS sensor,to_jsonb(g) AS gateway,to_jsonb(z) AS zone
      FROM sensor s JOIN gateway g ON g.id=s.gateway_id JOIN zone z ON z.id=g.zone_id WHERE s.id=:sid"""),
            {"sid": sensor_id},
        )
        .mappings()
        .one()
    )
    sensor, gateway, zone = (metadata[k] for k in ("sensor", "gateway", "zone"))
    header = "".join(
        f"# {label}: {safe_text(value)}\n"
        for label, value in (
            ("Zone", zone.get("name", "")),
            ("Type", zone.get("zone_type", "")),
            ("Location", zone.get("location", "")),
            ("Gateway", gateway.get("name") or gateway.get("hardware_id", "")),
            ("Sensor", sensor.get("name") or sensor.get("mac_address", "")),
            ("Timezone", "UTC"),
        )
    )
    if zone.get("latitude") is not None and zone.get("longitude") is not None:
        header += f"# GPS: {safe_text(zone['latitude'])}, {safe_text(zone['longitude'])}\n"
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zipped:
        size = 0
        for index, item in enumerate(records):
            out = io.StringIO()
            out.write(header)
            writer = csv.writer(out)
            writer.writerow(
                [
                    "timestamp_utc",
                    "value",
                    "unit",
                    "resolution_seconds",
                    "minimum",
                    "maximum",
                    "rms",
                    "standard_deviation",
                    "coverage_ratio",
                    "signal_source",
                ]
            )
            for row in item["data"]:
                writer.writerow(
                    [
                        row["timestamp"],
                        row["value"],
                        safe_text(item["unit"]),
                        row["resolution_seconds"],
                        row["minimum"],
                        row["maximum"],
                        row["rms"],
                        row["standard_deviation"],
                        row["coverage_ratio"],
                        row["signal_source"],
                    ]
                )
                if out.tell() + size > settings.sensor_export_max_bytes:
                    raise HTTPException(413, "Export exceeds byte limit")
            value = out.getvalue().encode()
            size += len(value)
            if size > settings.sensor_export_max_bytes:
                raise HTTPException(413, "Export exceeds byte limit")
            kind = re.sub(r"[^A-Za-z0-9_-]", "_", item["kind"])[:80] or "reading"
            zipped.writestr(f"{index + 1:02d}-{kind}.csv", value)
    return Response(
        target.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="sensor-data.zip"'},
    )


@app.get("/api/v1/visualization/sensors/{sensor_id}/waveform")
@limiter.limit("12/minute")
def waveform(
    request: Request,
    sensor_id: uuid.UUID,
    at: datetime,
    seconds: int = Query(10, ge=1, le=30),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.services.wav_service import _get_s3_client

    authorize(db, user, sensor_id)
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    item = (
        db.execute(
            text("""SELECT w.*,f.source_sha256 FROM wav_file w JOIN wav_feature f ON f.wav_file_id=w.id
      WHERE w.sensor_id=:sid AND w.started_at<=:at AND w.ended_at>:at AND w.feature_status='verified'
      AND w.raw_deleted_at IS NULL AND w.timing_status IN ('complete','inferred') AND w.coverage_ratio>=0.999
      ORDER BY w.started_at DESC LIMIT 1"""),
            {"sid": sensor_id, "at": at},
        )
        .mappings()
        .first()
    )
    if not item:
        raise HTTPException(
            404, "Für diesen Zeitpunkt ist kein zeitlich zuordenbares WAV verfügbar."
        )
    response = _get_s3_client().get_object(Bucket="greenmind-raw", Key=item["s3_key"])
    try:
        if response["ContentLength"] > 16 * 1024**2:
            raise HTTPException(413, "WAV too large for bounded preview")
        payload = response["Body"].read(16 * 1024**2 + 1)
    finally:
        response["Body"].close()
    if hashlib.sha256(payload).hexdigest() != item["source_sha256"]:
        raise HTTPException(503, "WAV verification failed")
    with wave.open(io.BytesIO(payload), "rb") as wav:
        rate = wav.getframerate()
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or rate > 1000:
            raise HTTPException(422, "Unsupported preview profile")
        first = max(0, int((at - item["started_at"]).total_seconds() * rate))
        if first >= wav.getnframes():
            raise HTTPException(404, "No samples at this timestamp")
        wav.setpos(first)
        values = (
            np.frombuffer(wav.readframes(seconds * rate), dtype="<i2").astype(np.float64)
            * item["pcm_scale_mv"]
            + item["pcm_offset_mv"]
        )
    return {
        "sample_rate": rate,
        "unit": "mV",
        "source": "verified_wav",
        "data": [
            {
                "timestamp": (
                    item["started_at"] + timedelta(seconds=(first + i) / rate)
                ).isoformat(),
                "value": round(float(value), 4),
            }
            for i, value in enumerate(values)
        ],
    }


# Direct routes use their own PostgreSQL read model and never alias Gateway IDs.
from app.visualization.direct_api import install as install_direct_views  # noqa: E402

install_direct_views(app)
