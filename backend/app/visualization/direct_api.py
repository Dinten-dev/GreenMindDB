"""Tenant-authorized Direct charts, exports and original recordings on the read sidecar."""

import csv
import io
import math
import uuid
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models.user import User
from app.rate_limit import limiter
from app.visualization import direct
from app.zone_access import require_zone_access

WINDOWS = {"5m": 300, "1h": 3600, "24h": 86400, "7d": 604800, "30d": 2592000}


def connection():
    resources = direct.resources()
    if not resources:
        raise HTTPException(503, "Direct-Messansicht noch nicht aktiviert")
    with Session(resources[0]) as db:
        yield db


def authorize(db, legacy, user, device_id):
    if not user.organization_id:
        raise HTTPException(403, "No organization")
    row = (
        db.execute(
            text("SELECT id,zone_id,mode FROM direct_device WHERE id=:id AND organization_id=:org"),
            {"id": str(device_id), "org": str(user.organization_id)},
        )
        .mappings()
        .first()
    )
    if not row:
        raise HTTPException(404, "Sensor not found")
    require_zone_access(legacy, user, row["zone_id"])
    return row


def get_series(db, device_id, range):
    end = datetime.now(UTC).timestamp()
    seconds = WINDOWS[range]
    step = 1 if seconds <= 3600 else 60 if seconds <= 604800 else 600
    try:
        return direct.query_series(db, str(device_id), end - seconds, end, step)
    except ValueError as exc:
        raise HTTPException(413, "Bitte kürzeren Zeitraum wählen") from exc


def recordings(db, device_id, start, end):
    # Latest verified revision only. Never send storage credentials or object keys.
    rows = (
        db.execute(
            text("""SELECT s.id AS segment_id,r.revision,r.manifest FROM direct_segment s
        JOIN direct_revision r ON r.segment_id=s.id AND r.revision=s.published_revision
        WHERE s.device_id=:id AND (s.bucket+1)*600>:start AND s.bucket*600<:end
          AND r.raw_deleted_at IS NULL ORDER BY s.bucket DESC LIMIT 101"""),
            {"id": str(device_id), "start": start, "end": end},
        )
        .mappings()
        .all()
    )
    result = []
    for row in rows:
        cfg = row["manifest"]["config"]
        for index, run in enumerate(row["manifest"]["runs"]):
            stamp = run["started_at_us"] / 1e6
            duration = run["frame_count"] / cfg["sample_rate"]
            if stamp < end and stamp + duration > start:
                result.append(
                    {
                        "segment_id": row["segment_id"],
                        "revision": row["revision"],
                        "run": index,
                        "started_at": datetime.fromtimestamp(stamp, UTC).isoformat(),
                        "duration_seconds": duration,
                        "sample_rate": cfg["sample_rate"],
                        "channels": cfg["channels"],
                        "sample_bits": cfg["sample_bits"],
                    }
                )
    return sorted(result, key=lambda r: r["started_at"], reverse=True)[:100]


def install(app):
    @app.get("/api/v1/visualization/direct/{device_id}/data")
    def data(
        device_id: uuid.UUID,
        range: str = Query("24h", pattern="^(5m|1h|24h|7d|30d)$"),
        user: User = Depends(get_current_user),
        legacy: Session = Depends(get_db),
        db: Session = Depends(connection),
    ):
        device = authorize(db, legacy, user, device_id)
        result = get_series(db, device_id, range)
        updated = db.execute(
            text(
                "SELECT max(v.updated_at) FROM direct_visual_segment v JOIN direct_segment s ON s.id=v.segment_id WHERE s.device_id=:id"
            ),
            {"id": str(device_id)},
        ).scalar()
        for series in result:
            series["analytics_scope"] = "comparison" if device["mode"] == "DUAL" else "direct"
            series["updated_at"] = updated.isoformat() if updated else None
        return result

    @app.get("/api/v1/visualization/direct/{device_id}/export")
    @limiter.limit("3/minute")
    def export(
        request: Request,
        device_id: uuid.UUID,
        range: str = Query("24h", pattern="^(5m|1h|24h|7d|30d)$"),
        user: User = Depends(get_current_user),
        legacy: Session = Depends(get_db),
        db: Session = Depends(connection),
    ):
        authorize(db, legacy, user, device_id)
        series = get_series(db, device_id, range)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "timestamp_utc",
                "channel",
                "unit",
                "mean",
                "minimum",
                "maximum",
                "rms",
                "standard_deviation",
                "resolution_seconds",
                "sample_count",
                "coverage_ratio",
                "source",
            ]
        )
        for item in series:
            for p in item["data"]:
                writer.writerow(
                    [
                        p["timestamp"],
                        item["kind"],
                        item["unit"],
                        p["value"],
                        p["minimum"],
                        p["maximum"],
                        p["rms"],
                        p["standard_deviation"],
                        p["resolution_seconds"],
                        p["reading_count"],
                        p["coverage_ratio"],
                        p["signal_source"],
                    ]
                )
        payload = output.getvalue().encode()
        if len(payload) > 8_000_000:
            raise HTTPException(413, "Export zu gross")
        return Response(
            payload,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="direct-{device_id}-{range}.csv"'
            },
        )

    @app.get("/api/v1/visualization/direct/{device_id}/recordings")
    def wav_list(
        device_id: uuid.UUID,
        range: str = Query("24h", pattern="^(5m|1h|24h|7d|30d)$"),
        user: User = Depends(get_current_user),
        legacy: Session = Depends(get_db),
        db: Session = Depends(connection),
    ):
        authorize(db, legacy, user, device_id)
        end = datetime.now(UTC).timestamp()
        return recordings(db, device_id, end - WINDOWS[range], end)

    @app.get("/api/v1/visualization/direct/{device_id}/wav/{segment_id}/{revision}/{run_index}")
    @limiter.limit("12/minute")
    def wav_download(
        request: Request,
        device_id: uuid.UUID,
        segment_id: uuid.UUID,
        revision: int,
        run_index: int,
        user: User = Depends(get_current_user),
        legacy: Session = Depends(get_db),
        db: Session = Depends(connection),
    ):
        authorize(db, legacy, user, device_id)
        row = db.execute(
            text("""SELECT r.manifest FROM direct_revision r JOIN direct_segment s ON s.id=r.segment_id
            WHERE s.id=:segment AND s.device_id=:device AND r.revision=:revision AND r.raw_deleted_at IS NULL"""),
            {"segment": str(segment_id), "device": str(device_id), "revision": revision},
        ).scalar()
        if not row or not 0 <= run_index < len(row["runs"]):
            raise HTTPException(404, "Aufnahme nicht gefunden")
        try:
            payload, _ = direct.read_wav(
                direct.resources()[1],
                row["runs"][run_index],
                row["config"],
                str(device_id),
                row["session_id"],
            )
        except (ValueError, OSError) as exc:
            raise HTTPException(503, "Aufnahme konnte nicht geprüft werden") from exc
        return Response(
            payload,
            media_type="audio/wav",
            headers={
                "Content-Disposition": f'attachment; filename="direct-{segment_id}-{run_index}.wav"'
            },
        )

    @app.get("/api/v1/visualization/direct/{device_id}/waveform")
    @limiter.limit("12/minute")
    def waveform(
        request: Request,
        device_id: uuid.UUID,
        at: datetime,
        seconds: int = Query(10, ge=1, le=30),
        channel: int = Query(0, ge=0, le=3),
        user: User = Depends(get_current_user),
        legacy: Session = Depends(get_db),
        db: Session = Depends(connection),
    ):
        authorize(db, legacy, user, device_id)
        stamp = at.replace(tzinfo=UTC).timestamp() if at.tzinfo is None else at.timestamp()
        segment = db.execute(
            text("""SELECT id FROM direct_segment WHERE device_id=:device
            AND (bucket+1)*600>:stamp AND bucket*600<:until ORDER BY bucket LIMIT 1"""),
            {"device": str(device_id), "stamp": stamp, "until": stamp + 600},
        ).scalar()
        if not segment:
            raise HTTPException(404, "Keine Originaldaten für diesen Zeitpunkt")
        source = direct.load_source(*direct.resources(), segment)
        if not source:
            raise HTTPException(
                409, "Originalaufnahme wird gerade fertiggestellt; bitte erneut versuchen"
            )
        _, cfg, runs, _ = source
        if seconds * cfg["sample_rate"] > 30000:
            raise HTTPException(413, "Originalvorschau enthält zu viele Messpunkte")
        if channel >= cfg["channels"]:
            raise HTTPException(404, "Kanal nicht gefunden")
        points = []
        origin = cfg["session_start_us"] / 1e6
        start_frame = max(0, math.ceil((stamp - origin) * cfg["sample_rate"]))
        end_frame = None
        cursor = None
        unit = ""
        for first, payload in runs:
            values, unit = direct.decode(payload, cfg)
            start = max(first, start_frame)
            if start >= first + len(values):
                continue
            if cursor is not None and start != cursor:
                break  # Never draw a line across missing samples.
            if end_frame is None:
                end_frame = start + seconds * cfg["sample_rate"]
            stop = min(first + len(values), end_frame)
            for index in range(start, stop):
                points.append(
                    {
                        "timestamp": datetime.fromtimestamp(
                            origin + index / cfg["sample_rate"], UTC
                        ).isoformat(),
                        "value": float(values[index - first, channel]),
                    }
                )
            cursor = stop
            if stop == end_frame:
                break
        if not points:
            raise HTTPException(404, "Keine Originaldaten für diesen Zeitpunkt")
        return {
            "sensor_id": str(device_id),
            "sample_rate": cfg["sample_rate"],
            "unit": unit,
            "data": points,
        }
