"""Independent Direct read model. Never writes Gateway or Direct ingestion records."""

import argparse
import hashlib
import io
import json
import logging
import math
import os
import time
import wave
from collections import defaultdict
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import numpy as np
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.direct.config import DirectSettings
from app.direct.models import Chunk, Piece, Revision, Segment
from app.direct.models import Session as CaptureSession
from app.direct.storage import ArtifactStore

log = logging.getLogger(__name__)


def fine_days():
    return int(os.environ.get("VISUAL_DIRECT_FINE_DAYS", "1"))


def resolution_for(end_seconds, now):
    if end_seconds >= now - fine_days() * 86400:
        return 1
    return 60 if end_seconds >= now - 7 * 86400 else 600


@lru_cache(maxsize=1)
def resources():
    if os.environ.get("VISUAL_DIRECT_ENABLED") != "true":
        return None
    cfg = DirectSettings(max_segment_bytes=8_000_000)
    engine = create_engine(
        cfg.database_url.get_secret_value(),
        pool_size=1,
        max_overflow=1,
        pool_pre_ping=True,
        pool_timeout=3,
        connect_args={"options": "-c statement_timeout=8000 -c lock_timeout=150 -c work_mem=8192"},
    )
    return engine, ArtifactStore(cfg)


def initialize(engine):
    with engine.begin() as db:
        db.execute(text(Path(__file__).with_name("direct_schema.sql").read_text()))


def decode(payload, cfg):
    width = cfg["sample_bits"] // 8
    if width not in (2, 3) or len(payload) % (width * cfg["channels"]):
        raise ValueError("Invalid PCM length")
    if width == 2:
        values = np.frombuffer(payload, dtype="<i2").astype(np.float64)
    else:
        b = np.frombuffer(payload, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        values = b[:, 0] | b[:, 1] << 8 | b[:, 2] << 16
        values = ((values ^ 0x800000) - 0x800000).astype(np.float64)
    legacy = cfg["calibration_version"] == "unsigned-mv-linear-int16-v1"
    if legacy:
        if width != 2 or cfg["channels"] != 1:
            raise ValueError("Invalid legacy calibration")
        # Same inverse mapping as the unchanged Gateway PCM16 WAV writer.
        values *= 3300.0 / 32767.0
    return values.reshape(-1, cfg["channels"]), "mV" if legacy else "adc_counts"


def aggregate(runs, cfg, seconds):
    """Split at exact UTC boundaries; count only received samples, including peaks."""
    stats = {}
    cursor = -1
    for first_frame, payload in runs:
        values, unit = decode(payload, cfg)
        if first_frame < cursor:
            raise ValueError("Overlapping source samples")
        cursor = first_frame + len(values)
        stamps = (
            cfg["session_start_us"]
            + (np.arange(len(values), dtype=np.int64) + first_frame)
            * 1_000_000
            // cfg["sample_rate"]
        )
        buckets = stamps // (seconds * 1_000_000) * seconds
        cuts = np.r_[0, np.flatnonzero(np.diff(buckets)) + 1, len(values)]
        for a, b in zip(cuts[:-1], cuts[1:], strict=True):
            for channel in range(cfg["channels"]):
                sample = values[a:b, channel]
                if not len(sample):
                    continue
                key = (int(buckets[a]), channel)
                row = dict(
                    bucket=key[0],
                    channel=channel,
                    unit=unit,
                    seconds=seconds,
                    n=len(sample),
                    total=float(sample.sum()),
                    total2=float(np.square(sample).sum()),
                    minimum=float(sample.min()),
                    maximum=float(sample.max()),
                    duration=len(sample) / cfg["sample_rate"],
                )
                if key in stats:
                    target = stats[key]
                    for name in ("n", "total", "total2", "duration"):
                        target[name] += row[name]
                    target["minimum"] = min(target["minimum"], row["minimum"])
                    target["maximum"] = max(target["maximum"], row["maximum"])
                else:
                    stats[key] = row
    return list(stats.values())


def read_wav(store, run, cfg, device_id, session_id):
    prefix = f"direct/{device_id}/{session_id}/"
    if not run["key"].startswith(prefix):
        raise ValueError("Artifact identity mismatch")
    payload = store.get(run["key"])
    if hashlib.sha256(payload).hexdigest() != run["sha256"]:
        raise ValueError("WAV checksum mismatch")
    with wave.open(io.BytesIO(payload), "rb") as wav:
        if (
            wav.getnchannels(),
            wav.getsampwidth(),
            wav.getframerate(),
            wav.getnframes(),
            wav.getcomptype(),
        ) != (
            cfg["channels"],
            cfg["sample_bits"] // 8,
            cfg["sample_rate"],
            run["frame_count"],
            "NONE",
        ):
            raise ValueError("WAV format mismatch")
        raw = wav.readframes(wav.getnframes())
    if len(raw) != run["frame_count"] * cfg["channels"] * cfg["sample_bits"] // 8:
        raise ValueError("Truncated WAV")
    return payload, raw


def load_source(engine, store, segment_id):
    # Capture one consistent revision; network reads happen after releasing DB snapshot.
    with Session(engine) as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        seg = db.get(Segment, segment_id)
        cfg = dict(db.get(CaptureSession, (seg.device_id, seg.session_id)).config)
        metadata = {
            key: getattr(seg, key)
            for key in ("id", "device_id", "session_id", "revision", "bucket")
        }
        published = db.get(Revision, (seg.id, seg.revision))
        if published and not published.raw_deleted_at:
            manifest = published.manifest
            canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
            if hashlib.sha256(canonical).hexdigest() != published.manifest_sha256:
                raise ValueError("Manifest checksum mismatch")
            if (
                manifest["device_id"],
                manifest["session_id"],
                manifest["revision"],
                manifest["config"],
            ) != (seg.device_id, seg.session_id, seg.revision, cfg):
                raise ValueError("Manifest identity mismatch")
            runs = None
        else:
            manifest = None
            rows = (
                db.query(Piece, Chunk)
                .join(Chunk, Piece.chunk_id == Chunk.id)
                .filter(Piece.segment_id == seg.id)
                .order_by(Chunk.first_frame)
                .limit(1201)
                .all()
            )
            if len(rows) > 1200:
                raise ValueError("Segment too large")
            runs = []
            frame_bytes = cfg["channels"] * cfg["sample_bits"] // 8
            for piece, chunk in rows:
                if chunk.payload is None:
                    # Old raw blocks may already be archived. Wait for the assembler's
                    # matching verified revision rather than replacing data incompletely.
                    return None
                if hashlib.sha256(chunk.payload).hexdigest() != chunk.payload_sha256:
                    raise ValueError("Chunk checksum mismatch")
                a = piece.payload_offset * frame_bytes
                b = a + piece.frame_count * frame_bytes
                if len(chunk.payload[a:b]) != b - a:
                    raise ValueError("Truncated chunk")
                runs.append((chunk.first_frame + piece.payload_offset, chunk.payload[a:b]))
    if manifest:
        runs = [
            (
                run["first_frame"],
                read_wav(store, run, cfg, metadata["device_id"], metadata["session_id"])[1],
            )
            for run in manifest["runs"]
        ]
    if sum(len(payload) for _, payload in runs) > 8_000_000:
        raise ValueError("Visualization segment exceeds memory budget")
    return metadata, cfg, runs, "wav" if manifest else "readings"


def refresh_segment(engine, store, segment_id, now):
    source = load_source(engine, store, segment_id)
    if source is None:
        return False
    meta, cfg, runs, provenance = source
    seconds = resolution_for((meta["bucket"] + 1) * 600, now)
    rows = aggregate(runs, cfg, seconds)
    with engine.begin() as db:
        # A single worker lock serializes replacements, keeping readers on the old
        # complete version until the new data and its marker commit together.
        db.execute(
            text("""INSERT INTO direct_visual_segment(segment_id,source_revision,seconds,source,updated_at)
            VALUES (:id,:revision,:seconds,:source,now()) ON CONFLICT(segment_id) DO UPDATE SET
            source_revision=EXCLUDED.source_revision,seconds=EXCLUDED.seconds,source=EXCLUDED.source,
            updated_at=now(),error=NULL,retry_at=0"""),
            meta | {"seconds": seconds, "source": provenance},
        )
        db.execute(text("DELETE FROM direct_visual_point WHERE segment_id=:id"), meta)
        if rows:
            db.execute(
                text("""INSERT INTO direct_visual_point
                (segment_id,device_id,bucket,channel,unit,seconds,n,total,total2,minimum,maximum,duration)
                VALUES (:segment_id,:device_id,:bucket,:channel,:unit,:seconds,:n,:total,:total2,:minimum,:maximum,:duration)"""),
                [row | {"segment_id": meta["id"], "device_id": meta["device_id"]} for row in rows],
            )
    return True


def cycle(engine, store, now=None):
    now = now or time.time()
    if os.getloadavg()[0] > float(os.environ.get("VISUAL_MAX_HOST_LOAD", "2.4")):
        return "paused_for_host_load"
    with engine.connect() as db:
        candidates = (
            db.execute(
                text("""WITH due AS (SELECT s.id,s.updated_at,coalesce(v.updated_at,to_timestamp(0)) AS checked FROM direct_segment s
            LEFT JOIN direct_visual_segment v ON v.segment_id=s.id
            WHERE (v.segment_id IS NULL OR v.source_revision<>s.revision OR
              (v.source<>'wav' AND s.published_revision=s.revision) OR
              v.seconds<CASE WHEN (s.bucket+1)*600>=:fine THEN 1
                WHEN (s.bucket+1)*600>=:week THEN 60 ELSE 600 END)
              AND coalesce(v.retry_at,0)<=:now
             )
            (SELECT id FROM due WHERE updated_at>:now-120 ORDER BY checked,updated_at DESC LIMIT 2)
            UNION ALL
            (SELECT id FROM due WHERE updated_at<=:now-120 ORDER BY checked,updated_at DESC LIMIT 1)"""),
                {"fine": now - fine_days() * 86400, "week": now - 7 * 86400, "now": now},
            )
            .scalars()
            .all()
        )
    failures = 0
    for segment_id in candidates:
        try:
            refresh_segment(engine, store, segment_id, now)
        except Exception as exc:
            failures += 1
            log.warning("direct_visual_segment_failed type=%s", type(exc).__name__)
            with engine.begin() as db:
                db.execute(
                    text("""INSERT INTO direct_visual_segment(segment_id,source_revision,seconds,source,error,retry_at)
                    VALUES (:id,0,1,'pending',:error,:retry) ON CONFLICT(segment_id)
                    DO UPDATE SET error=:error,retry_at=:retry"""),
                    {"id": segment_id, "error": type(exc).__name__, "retry": now + 60},
                )
    return "degraded" if failures else "healthy"


def query_series(db, device_id, start, end, step):
    rows = (
        db.execute(
            text("""SELECT p.channel,p.unit,(p.bucket/greatest(p.seconds,:step))*greatest(p.seconds,:step) AS bucket,
        greatest(p.seconds,:step) AS seconds,sum(p.n) AS n,sum(p.total) AS total,sum(p.total2) AS total2,
        min(p.minimum) AS minimum,max(p.maximum) AS maximum,sum(p.duration) AS duration,
        bool_and(v.source='wav') AS verified
        FROM direct_visual_point p JOIN direct_visual_segment v ON v.segment_id=p.segment_id
        WHERE p.device_id=:device AND p.bucket<:end AND p.bucket+p.seconds>:start
        GROUP BY channel,unit,(p.bucket/greatest(p.seconds,:step))*greatest(p.seconds,:step),greatest(p.seconds,:step)
        ORDER BY bucket LIMIT 20001"""),
            {"device": device_id, "start": int(start), "end": int(end), "step": step},
        )
        .mappings()
        .all()
    )
    if len(rows) > 20000:
        raise ValueError("Too many points")
    series = defaultdict(list)
    for r in rows:
        mean = r["total"] / int(r["n"])
        rms = math.sqrt(max(0, r["total2"] / int(r["n"])))
        series[(r["channel"], r["unit"])].append(
            {
                "timestamp": datetime.fromtimestamp(r["bucket"], UTC).isoformat(),
                "value": round(mean, 4),
                "minimum": r["minimum"],
                "maximum": r["maximum"],
                "rms": rms,
                "standard_deviation": math.sqrt(max(0, rms * rms - mean * mean)),
                "resolution_seconds": r["seconds"],
                "reading_count": int(r["n"]),
                "coverage_ratio": min(1, r["duration"] / r["seconds"]),
                "signal_source": "overlap"
                if r["duration"] > r["seconds"] * 1.02
                else "wav"
                if r["verified"]
                else "readings",
            }
        )
    return [
        {
            "sensor_id": device_id,
            "kind": f"bio_signal_ch{channel + 1}",
            "unit": unit,
            "source": "direct",
            "data": points,
            "original_signal_available": True,
            "aggregation": f"{fine_days()}d:1s;7d:1m;older:10m",
        }
        for (channel, unit), points in series.items()
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("init", "once", "run"))
    args = parser.parse_args()
    engine, store = resources()
    if args.action == "init":
        initialize(engine)
        return
    with engine.connect() as guard:
        if not guard.execute(text("SELECT pg_try_advisory_lock(71920261)")).scalar_one():
            raise RuntimeError("Direct visualization worker already running")
        guard.commit()
        while True:
            try:
                status = cycle(engine, store)
                with engine.begin() as db:
                    db.execute(
                        text(
                            "INSERT INTO direct_visual_worker(id,status,updated_at) VALUES (1,:status,now()) ON CONFLICT(id) DO UPDATE SET status=:status,updated_at=now()"
                        ),
                        {"status": status},
                    )
            except Exception as exc:
                log.error("direct_visual_worker_retry type=%s", type(exc).__name__)
            if args.action == "once":
                return
            time.sleep(10)


if __name__ == "__main__":
    main()
