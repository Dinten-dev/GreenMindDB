"""Authenticated, bounded jobs for original WAV exports; disabled by default."""

import hashlib
import json
import time
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models.master import Gateway, Sensor, Zone
from app.models.user import User
from app.models.wav_file import WavFile
from app.rate_limit import limiter
from app.visualization.archive_jobs import EXPIRES, MAX_BYTES, MAX_FILES, connect, root
from app.visualization.archive_jobs import PART_BYTES as PART_BYTES
from app.zone_access import zone_access_filter


def authorize_gateway(db, user, sensor_id):
    if (
        not user.organization_id
        or not db.query(Sensor.id)
        .join(Gateway, Gateway.id == Sensor.gateway_id)
        .join(Zone, Zone.id == Gateway.zone_id)
        .filter(Sensor.id == sensor_id, zone_access_filter(user))
        .first()
    ):
        raise HTTPException(404, "Sensor nicht gefunden")


def authorize_direct(legacy, user, device_id):
    from app.visualization import direct
    from app.visualization.direct_api import authorize

    with Session(direct.resources()[0]) as session:
        authorize(session, legacy, user, device_id)


def select_gateway(db, sensor_id, start, end):
    rows = (
        db.query(WavFile)
        .filter(WavFile.sensor_id == sensor_id, WavFile.started_at < end, WavFile.ended_at > start)
        .order_by(WavFile.started_at, WavFile.id)
        .limit(MAX_FILES + 1)
        .yield_per(10)
    )
    return [
        {
            "kind": "gateway",
            "bucket": "greenmind-raw",
            "key": r.s3_key,
            "sha256": r.content_sha256,
            "size": r.file_size_bytes,
            "name": f"{r.started_at:%Y%m%dT%H%M%S}_{r.id}.wav",
            "started_at": r.started_at.isoformat(),
            "sample_rate": r.sample_rate,
            "channels": 1,
            "sample_bits": 16,
            "scale_mv": r.pcm_scale_mv,
            "offset_mv": r.pcm_offset_mv,
            "calibration": r.calibration_version,
            "encoding": r.pcm_encoding_version,
            "coverage_ratio": r.coverage_ratio,
        }
        for r in rows
    ]


def select_direct(device_id, start, end):
    from app.visualization import direct

    with Session(direct.resources()[0]) as session:
        rows = session.execute(
            text("""SELECT r.manifest,r.manifest_sha256 FROM direct_revision r
          JOIN direct_segment s ON s.id=r.segment_id
          WHERE s.device_id=:device AND r.revision=s.published_revision
          AND s.sealed AND r.verified_at IS NOT NULL
          AND (s.bucket+1)*600>:start AND s.bucket*600<:end
          ORDER BY s.bucket LIMIT :limit"""),
            {
                "device": str(device_id),
                "start": start.timestamp(),
                "end": end.timestamp(),
                "limit": 501,
            },
        ).yield_per(10)
        return _direct_items(rows, start, end)


def _direct_items(rows, start, end):
    items = []
    from app.direct.config import DirectSettings

    bucket = DirectSettings().s3_bucket
    for position, (manifest, digest) in enumerate(rows):
        if position >= 500:
            raise HTTPException(413, "Zeitraum aufteilen: zu viele Direct-Segmente")
        canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        if hashlib.sha256(canonical).hexdigest() != digest:
            raise HTTPException(503, "Direct-Manifest nicht verifiziert")
        cfg = manifest["config"]
        for number, run in enumerate(manifest["runs"]):
            stamp = datetime.fromtimestamp(run["started_at_us"] / 1e6, UTC)
            size = 44 + run["frame_count"] * cfg["channels"] * cfg["sample_bits"] // 8
            if (
                stamp < end
                and stamp + timedelta(seconds=run["frame_count"] / cfg["sample_rate"]) > start
            ):
                items.append(
                    {
                        "kind": "direct",
                        "bucket": bucket,
                        "key": run["key"],
                        "sha256": run["sha256"],
                        "size": size,
                        "name": f"{stamp:%Y%m%dT%H%M%S}_{manifest['revision']}_{number}_{run['sha256'][:12]}.wav",
                        "started_at": stamp.isoformat(),
                        "sample_rate": cfg["sample_rate"],
                        "channels": cfg["channels"],
                        "sample_bits": cfg["sample_bits"],
                        "scale_mv": cfg.get("scale_mv"),
                        "offset_mv": cfg.get("offset_mv"),
                        "calibration": cfg.get("calibration_version"),
                        "missing_frame_ranges": manifest.get("missing_frame_ranges", []),
                    }
                )
            if len(items) > MAX_FILES:
                break
    return items


def checked_job(db, user, job_id, legacy):
    row = db.execute(
        "SELECT * FROM jobs WHERE id=? AND user_id=?", (str(job_id), str(user.id))
    ).fetchone()
    if not row or row["created"] + EXPIRES < time.time():
        raise HTTPException(404, "Export nicht gefunden")
    sensor_id = uuid.UUID(row["sensor_id"])
    if row["kind"] == "gateway":
        authorize_gateway(legacy, user, sensor_id)
    else:
        authorize_direct(legacy, user, sensor_id)
    return row


def install(app):
    @app.post("/api/v1/visualization/archive-exports", status_code=202)
    @limiter.limit("3/minute")
    def create(
        request: Request,
        kind: str = Query(pattern="^(gateway|direct)$"),
        sensor_id: uuid.UUID = None,
        from_dt: datetime = None,
        to_dt: datetime = None,
        user: User = Depends(get_current_user),
        legacy: Session = Depends(get_db),
    ):
        if not sensor_id or not from_dt or not to_dt or not from_dt.tzinfo or not to_dt.tzinfo:
            raise HTTPException(400, "Sensor und Zeitgrenzen mit Zeitzone erforderlich")
        if to_dt <= from_dt or to_dt - from_dt > timedelta(days=3650):
            raise HTTPException(400, "Ungültiger Zeitraum")
        path = root()
        if kind == "gateway":
            authorize_gateway(legacy, user, sensor_id)
            items = select_gateway(legacy, sensor_id, from_dt, to_dt)
        else:
            authorize_direct(legacy, user, sensor_id)
            items = select_direct(sensor_id, from_dt, to_dt)
        if not items:
            raise HTTPException(404, "Keine WAV-Aufnahmen im Zeitraum")
        if len(items) > MAX_FILES or sum(item["size"] for item in items) > MAX_BYTES:
            raise HTTPException(413, "Zeitraum aufteilen: zu viele Originaldaten")
        if any(
            not isinstance(item["sha256"], str)
            or len(item["sha256"]) != 64
            or not 0 < item["size"] <= 64 * 1024**2
            for item in items
        ):
            raise HTTPException(409, "Aufnahme ohne verifizierbare Prüfsumme")
        with connect(path) as jobs:
            active = jobs.execute(
                "SELECT count(*) FROM jobs WHERE user_id=? AND status IN ('queued','working')",
                (str(user.id),),
            ).fetchone()[0]
            if active >= 2:
                raise HTTPException(429, "Zwei Exporte sind bereits offen")
            job_id = str(uuid.uuid4())
            jobs.execute(
                "INSERT INTO jobs(id,user_id,kind,sensor_id,created,status,items) VALUES (?,?,?,?,?,?,?)",
                (
                    job_id,
                    str(user.id),
                    kind,
                    str(sensor_id),
                    time.time(),
                    "queued",
                    json.dumps(items, separators=(",", ":")),
                ),
            )
        return {
            "id": job_id,
            "status": "queued",
            "files": len(items),
            "bytes": sum(item["size"] for item in items),
        }

    @app.get("/api/v1/visualization/archive-exports/{job_id}")
    def status(
        job_id: uuid.UUID, user: User = Depends(get_current_user), legacy: Session = Depends(get_db)
    ):
        with connect(root()) as jobs:
            row = checked_job(jobs, user, job_id, legacy)
            return {
                "id": row["id"],
                "status": row["status"],
                "parts": len(json.loads(row["parts"])),
                "completed": row["completed"],
                "total": len(json.loads(row["items"])),
                "error": row["error"],
            }

    @app.get("/api/v1/visualization/archive-exports/{job_id}/parts/{part}")
    @limiter.limit("12/minute")
    def download(
        request: Request,
        job_id: uuid.UUID,
        part: int,
        user: User = Depends(get_current_user),
        legacy: Session = Depends(get_db),
    ):
        path = root()
        with connect(path) as jobs:
            row = checked_job(jobs, user, job_id, legacy)
            parts = json.loads(row["parts"])
            if row["status"] != "ready" or not 0 <= part < len(parts):
                raise HTTPException(404, "Exportteil nicht verfügbar")
            name = f"{row['id']}-{part:03d}.zip"
        target = path / name
        if not target.is_file() or target.is_symlink():
            raise HTTPException(503, "Exportteil fehlt")
        return FileResponse(target, media_type="application/zip", filename=name)

    @app.delete("/api/v1/visualization/archive-exports/{job_id}")
    def cancel(
        job_id: uuid.UUID, user: User = Depends(get_current_user), legacy: Session = Depends(get_db)
    ):
        with connect(root()) as jobs:
            checked_job(jobs, user, job_id, legacy)
            jobs.execute(
                "UPDATE jobs SET status='cancelled' WHERE id=? AND status='queued'", (str(job_id),)
            )
        return {"id": str(job_id)}
