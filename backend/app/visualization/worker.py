"""Rate-limited historical compaction, independent from ingestion workers."""

import argparse
import hashlib
import io
import json
import logging
import os
import re
import time
import wave
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from sqlalchemy import create_engine, text

from app.visualization.core import bucket, merge_readings, pack_ids, unpack_ids
from app.visualization.storage import read_archive, write_archive

LOG = logging.getLogger(__name__)
ARCHIVE = Path(os.environ.get("VISUAL_ARCHIVE_DIR", "/archive"))
LOCK_ID = 719420260915


def engine_for_worker():
    return create_engine(
        os.environ["DATABASE_URL"], pool_size=1, max_overflow=1, pool_pre_ping=True
    )


def initialize(engine):
    with engine.begin() as db:
        db.execute(text("SET LOCAL lock_timeout='250ms'"))
        db.execute(text(Path(__file__).with_name("schema.sql").read_text()))
        exists = db.execute(
            text(
                "SELECT 1 FROM pg_trigger WHERE tgrelid='sensor_reading'::regclass AND tgname='visual_source_changed'"
            )
        ).scalar()
        if not exists:
            db.execute(
                text(
                    "CREATE TRIGGER visual_source_changed AFTER INSERT OR UPDATE OR DELETE ON sensor_reading FOR EACH ROW EXECUTE FUNCTION visual_track_historical_change()"
                )
            )


def set_status(engine, status, details):
    with engine.begin() as db:
        db.execute(
            text(
                "INSERT INTO visual_worker(name,status,details) VALUES ('compactor',:s,CAST(:d AS jsonb)) ON CONFLICT(name) DO UPDATE SET status=EXCLUDED.status,details=EXCLUDED.details,updated_at=now()"
            ),
            {"s": status, "d": json.dumps(details)},
        )


def promote(engine, now):
    """Merge complete minute windows once they are older than seven days."""
    cutoff = bucket(now - timedelta(days=7), 600)
    with engine.begin() as db:
        db.execute(text("SET LOCAL lock_timeout='250ms'"))
        db.execute(text("SET LOCAL statement_timeout='15s'"))
        keys = (
            db.execute(
                text(
                    "SELECT DISTINCT sensor_id,kind,time_bucket('10 minutes',bucket) AS target FROM visual_reading WHERE seconds=60 AND bucket < :cutoff ORDER BY target LIMIT 100"
                ),
                {"cutoff": cutoff},
            )
            .mappings()
            .all()
        )
        for key in keys:
            promote_key(db, dict(key))


def promote_key(db, key):
    rows = (
        db.execute(
            text(
                "SELECT * FROM visual_reading WHERE sensor_id=:sensor_id AND kind=:kind AND bucket>=:target AND bucket<:target+interval'10 minutes' FOR UPDATE"
            ),
            dict(key),
        )
        .mappings()
        .all()
    )
    if not rows or (len(rows) == 1 and rows[0]["seconds"] == 600):
        return
    if len({row["unit"] for row in rows}) != 1:
        raise RuntimeError("Mixed source units need explicit conversion")
    ids = set()
    combined = {
        "n": 0,
        "total": 0.0,
        "total2": 0.0,
        "minimum": float("inf"),
        "maximum": -float("inf"),
    }
    for row in rows:
        row_ids = unpack_ids(row["identities"])
        if ids.intersection(row_ids):
            raise RuntimeError("Overlapping minute identities during promotion")
        ids.update(row_ids)
        for field in ("n", "total", "total2"):
            combined[field] += row[field]
        combined["minimum"] = min(combined["minimum"], row["minimum"])
        combined["maximum"] = max(combined["maximum"], row["maximum"])
    db.execute(
        text(
            "DELETE FROM visual_reading WHERE sensor_id=:sensor_id AND kind=:kind AND bucket>=:target AND bucket<:target+interval'10 minutes'"
        ),
        dict(key),
    )
    save_reading(
        db,
        dict(key)
        | combined
        | {
            "bucket": key["target"],
            "seconds": 600,
            "unit": rows[0]["unit"],
            "identities": pack_ids(ids),
        },
    )


def save_reading(db, row):
    db.execute(
        text("""INSERT INTO visual_reading(sensor_id,kind,bucket,seconds,unit,n,total,total2,minimum,maximum,identities)
    VALUES (:sensor_id,:kind,:bucket,:seconds,:unit,:n,:total,:total2,:minimum,:maximum,:identities)
    ON CONFLICT(sensor_id,kind,bucket,seconds) DO UPDATE SET n=EXCLUDED.n,total=EXCLUDED.total,total2=EXCLUDED.total2,
    minimum=EXCLUDED.minimum,maximum=EXCLUDED.maximum,identities=EXCLUDED.identities"""),
        row,
    )


def track_chunks(engine, now):
    with engine.begin() as db:
        db.execute(
            text("""INSERT INTO visual_chunk(chunk_name,range_start,range_end)
        SELECT chunk_schema||'.'||chunk_name,range_start,range_end FROM timescaledb_information.chunks
        WHERE hypertable_name='sensor_reading' AND range_end<=:cutoff
        ON CONFLICT(chunk_name) DO NOTHING"""),
            {"cutoff": now - timedelta(hours=24)},
        )


def snapshot_chunk(engine, chunk, now):
    """Snapshot + aggregates share one MVCC view. Late writes change the marker."""
    name = chunk["chunk_name"]
    if not re.fullmatch(r"_timescaledb_internal\._hyper_\d+_\d+_chunk", name):
        raise ValueError("Unexpected chunk identifier")
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as db:
        with db.begin():
            db.execute(text("SET LOCAL statement_timeout='10min'"))
            db.execute(text("SET LOCAL lock_timeout='250ms'"))
            db.execute(text("SET LOCAL work_mem='8MB'"))
            db.execute(text("SET LOCAL max_parallel_workers_per_gather=0"))
            version = db.execute(
                text("SELECT change_version FROM visual_chunk WHERE chunk_name=:name"),
                {"name": name},
            ).scalar_one()
            groups = defaultdict(list)
            current = None
            protected_rows = 0

            def flush():
                for (sensor, kind, stamp, seconds, unit), values in groups.items():
                    if seconds == 600:
                        promote_key(db, {"sensor_id": sensor, "kind": kind, "target": stamp})
                    previous = (
                        db.execute(
                            text(
                                "SELECT * FROM visual_reading WHERE sensor_id=:sid AND kind=:kind AND bucket=:stamp AND seconds=:seconds FOR UPDATE"
                            ),
                            {"sid": sensor, "kind": kind, "stamp": stamp, "seconds": seconds},
                        )
                        .mappings()
                        .first()
                    )
                    if previous and previous["unit"] != unit:
                        raise RuntimeError("Source unit changed inside a time window")
                    state = merge_readings(previous, values)
                    save_reading(
                        db,
                        state
                        | {
                            "sensor_id": sensor,
                            "kind": kind,
                            "bucket": stamp,
                            "seconds": seconds,
                            "unit": unit,
                        },
                    )
                groups.clear()

            def records():
                nonlocal current, protected_rows
                # Timestamp index keeps buffering bounded to one ten-minute interval.
                rows = db.execute(
                    text(f"SELECT * FROM {name} ORDER BY timestamp").execution_options(
                        stream_results=True, max_row_buffer=1000
                    )
                )
                try:
                    for row in rows.mappings():
                        # Existing feature workers still consult these source fields.
                        # Retain chunks containing them until their archival reader is deployed.
                        if any(
                            row.get(k) is not None
                            for k in (
                                "source_sequence",
                                "source_dropped_samples_total",
                                "quality_valid_count",
                                "quality_lead_off_count",
                                "quality_rail_high_count",
                                "quality_rail_low_count",
                                "quality_jump_count",
                                "quality_recovery_count",
                            )
                        ):
                            protected_rows += 1
                        stamp = row["timestamp"]
                        interval = bucket(stamp, 600)
                        if current is not None and current != interval:
                            flush()
                            time.sleep(float(os.environ.get("VISUAL_BATCH_PAUSE", "0.05")))
                        current = interval
                        seconds = (
                            600
                            if interval + timedelta(seconds=600) <= now - timedelta(days=7)
                            else 60
                        )
                        groups[
                            (
                                str(row["sensor_id"]),
                                row["kind"],
                                bucket(stamp, seconds),
                                seconds,
                                row["unit"],
                            )
                        ].append(dict(row))
                        yield row
                    flush()
                finally:
                    rows.close()

            manifest = write_archive(ARCHIVE, records(), name.replace(".", "-"))
            manifest["protected_rows"] = protected_rows
            db.execute(
                text(
                    "UPDATE visual_chunk SET archived_version=:v,status='verified',archive_manifest=:manifest,source_rows=:rows,updated_at=now() WHERE chunk_name=:name"
                ),
                {
                    "v": version,
                    "manifest": json.dumps(manifest),
                    "rows": manifest["rows"],
                    "name": name,
                },
            )
    return manifest


def prune_chunk(engine, chunk):
    """Never wait behind production traffic; remove only the verified snapshot."""
    name = chunk["chunk_name"]
    if not re.fullmatch(r"_timescaledb_internal\._hyper_\d+_\d+_chunk", name):
        raise ValueError("Unexpected chunk identifier")
    # Verify the complete backup again before obtaining any exclusive lock.
    with engine.connect() as db:
        saved = (
            db.execute(text("SELECT * FROM visual_chunk WHERE chunk_name=:name"), {"name": name})
            .mappings()
            .one()
        )
        if not saved["archive_manifest"]:
            return False
        pending = db.execute(
            text("""SELECT count(*) FROM wav_file w LEFT JOIN visual_wav v ON v.wav_id=w.id
          WHERE w.started_at<:end AND w.ended_at>:start AND (w.feature_status IS DISTINCT FROM 'verified' OR coalesce(v.status,'pending') NOT IN ('verified','unavailable'))"""),
            {"start": saved["range_start"], "end": saved["range_end"]},
        ).scalar_one()
        if pending:
            return False
        manifest = json.loads(saved["archive_manifest"])
        if manifest.get("protected_rows", 0):
            return False
    for _ in read_archive(manifest):
        pass
    with engine.begin() as db:
        db.execute(text("SET LOCAL lock_timeout='150ms'"))
        db.execute(text("SET LOCAL statement_timeout='3s'"))
        # Only the historical chunk is locked; a busy chunk is skipped immediately.
        db.execute(text(f"LOCK TABLE ONLY {name} IN ACCESS EXCLUSIVE MODE NOWAIT"))
        state = (
            db.execute(
                text("SELECT * FROM visual_chunk WHERE chunk_name=:name FOR UPDATE NOWAIT"),
                {"name": name},
            )
            .mappings()
            .one()
        )
        if state["change_version"] != state["archived_version"] or state["status"] != "verified":
            return False
        # created_before/after are deliberately not used: exact chunk range only.
        removed = (
            db.execute(
                text(
                    "SELECT drop_chunks('sensor_reading',older_than=>CAST(:end AS timestamptz),newer_than=>CAST(:start AS timestamptz))"
                ),
                {"start": state["range_start"], "end": state["range_end"]},
            )
            .scalars()
            .all()
        )
        if len(removed) != 1 or str(removed[0]) != name:
            raise RuntimeError("Refusing unexpected chunk removal")
        db.execute(
            text(
                "UPDATE visual_chunk SET status='compacted',updated_at=now() WHERE chunk_name=:name"
            ),
            {"name": name},
        )
    return True


def process_wav(engine, item, now):
    from app.services.wav_service import _get_s3_client

    seconds = 600 if item["ended_at"] <= now - timedelta(days=7) else 60
    if item["timing_status"] not in ("complete", "inferred") or item["coverage_ratio"] < 0.999:
        # Sparse WAVs do not identify where missing samples occurred. Never invent timestamps.
        with engine.begin() as db:
            db.execute(
                text(
                    "INSERT INTO visual_wav(wav_id,source_sha256,seconds,status,error) VALUES (:id,:sha,:seconds,'unavailable','sparse_timing') ON CONFLICT(wav_id) DO UPDATE SET status='unavailable',seconds=EXCLUDED.seconds,error='sparse_timing',updated_at=now()"
                ),
                {"id": item["id"], "sha": item["source_sha256"], "seconds": seconds},
            )
        return
    response = _get_s3_client().get_object(Bucket="greenmind-raw", Key=item["s3_key"])
    try:
        if response["ContentLength"] > 16 * 1024**2:
            raise ValueError("WAV exceeds bounded visualization read")
        payload = response["Body"].read(16 * 1024**2 + 1)
    finally:
        response["Body"].close()
    if hashlib.sha256(payload).hexdigest() != item["source_sha256"]:
        raise ValueError("WAV checksum mismatch")
    groups = []
    with wave.open(io.BytesIO(payload), "rb") as wav:
        rate = wav.getframerate()
        if (
            wav.getnchannels() != 1
            or wav.getsampwidth() != 2
            or wav.getcomptype() != "NONE"
            or rate != item["sample_rate"]
        ):
            raise ValueError("Unsupported WAV profile")
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float64)
        samples = samples * item["pcm_scale_mv"] + item["pcm_offset_mv"]
        origin = item["started_at"]
        cursor = 0
        while cursor < len(samples):
            stamp = origin + timedelta(seconds=cursor / rate)
            start = bucket(stamp, seconds)
            stop = min(
                len(samples),
                max(
                    cursor + 1,
                    int(
                        round(
                            ((start + timedelta(seconds=seconds)) - origin).total_seconds() * rate
                        )
                    ),
                ),
            )
            values = samples[cursor:stop]
            groups.append(
                {
                    "wav_id": item["id"],
                    "sensor_id": item["sensor_id"],
                    "bucket": start,
                    "seconds": seconds,
                    "n": len(values),
                    "total": float(np.sum(values)),
                    "total2": float(np.dot(values, values)),
                    "minimum": float(np.min(values)),
                    "maximum": float(np.max(values)),
                    "duration": len(values) / rate,
                }
            )
            cursor = stop
    with engine.begin() as db:
        db.execute(
            text(
                "INSERT INTO visual_wav(wav_id,source_sha256,seconds,status) VALUES (:id,:sha,:seconds,'verified') ON CONFLICT(wav_id) DO UPDATE SET source_sha256=EXCLUDED.source_sha256,seconds=EXCLUDED.seconds,status='verified',error=NULL,updated_at=now()"
            ),
            {"id": item["id"], "sha": item["source_sha256"], "seconds": seconds},
        )
        db.execute(text("DELETE FROM visual_wave WHERE wav_id=:id"), {"id": item["id"]})
        db.execute(
            text(
                "INSERT INTO visual_wave(wav_id,sensor_id,bucket,seconds,n,total,total2,minimum,maximum,duration) VALUES (:wav_id,:sensor_id,:bucket,:seconds,:n,:total,:total2,:minimum,:maximum,:duration)"
            ),
            groups,
        )


def wave_candidates(engine, now, limit=20):
    with engine.connect() as db:
        return (
            db.execute(
                text("""SELECT w.*,f.source_sha256 FROM wav_file w JOIN wav_feature f ON f.wav_file_id=w.id
          LEFT JOIN visual_wav v ON v.wav_id=w.id WHERE w.feature_status='verified' AND w.raw_deleted_at IS NULL
          AND (v.wav_id IS NULL OR (v.seconds=60 AND v.status IN ('verified','unavailable') AND w.ended_at<:old) OR (v.status='failed' AND v.updated_at<now()-interval '1 day'))
          ORDER BY (w.started_at>=:recent) DESC,w.started_at DESC LIMIT :lim"""),
                {"old": now - timedelta(days=7), "recent": now - timedelta(days=7), "lim": limit},
            )
            .mappings()
            .all()
        )


def cycle(engine, prune=False):
    now = datetime.now(UTC)
    maximum_load = float(os.environ.get("VISUAL_MAX_HOST_LOAD", "0"))
    if maximum_load and os.getloadavg()[0] > maximum_load:
        set_status(engine, "paused_for_host_load", {"pruning_enabled": prune})
        return
    set_status(engine, "working", {"pruning_enabled": prune})
    promote(engine, now)
    for item in wave_candidates(engine, now):
        try:
            process_wav(engine, item, now)
        except Exception as exc:
            with engine.begin() as db:
                db.execute(
                    text(
                        "INSERT INTO visual_wav(wav_id,source_sha256,seconds,status,error) VALUES (:id,:sha,60,'failed',:error) ON CONFLICT(wav_id) DO UPDATE SET status='failed',error=EXCLUDED.error,updated_at=now()"
                    ),
                    {"id": item["id"], "sha": item["source_sha256"], "error": type(exc).__name__},
                )
        time.sleep(float(os.environ.get("VISUAL_WAV_PAUSE", "0.2")))
    track_chunks(engine, now)
    with engine.connect() as db:
        candidate = (
            db.execute(
                text(
                    "SELECT v.* FROM visual_chunk v JOIN timescaledb_information.chunks c ON v.chunk_name=c.chunk_schema||'.'||c.chunk_name WHERE v.status='pending' OR (v.change_version IS DISTINCT FROM v.archived_version AND v.updated_at<now()-interval '1 minute') ORDER BY range_start DESC LIMIT 1"
                )
            )
            .mappings()
            .first()
        )
    if candidate:
        snapshot_chunk(engine, candidate, now)
    if prune:
        with engine.connect() as db:
            candidate = (
                db.execute(
                    text(
                        "SELECT c.* FROM visual_chunk c WHERE c.status='verified' AND coalesce((c.archive_manifest::jsonb->>'protected_rows')::bigint,0)=0 AND NOT EXISTS (SELECT 1 FROM wav_file w LEFT JOIN visual_wav v ON v.wav_id=w.id WHERE w.started_at<c.range_end AND w.ended_at>c.range_start AND (w.feature_status IS DISTINCT FROM 'verified' OR coalesce(v.status,'pending') NOT IN ('verified','unavailable'))) ORDER BY c.range_end DESC LIMIT 1"
                    )
                )
                .mappings()
                .first()
            )
        if candidate:
            prune_chunk(engine, candidate)
    set_status(engine, "healthy", {"pruning_enabled": prune})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["init", "once", "run", "restore"])
    parser.add_argument("--prune", action="store_true")
    parser.add_argument("--manifest")
    args = parser.parse_args()
    engine = engine_for_worker()
    if args.action == "init":
        initialize(engine)
        return
    if args.action == "restore":
        if not args.manifest:
            raise SystemExit("--manifest JSON file is required")
        manifest = json.loads(Path(args.manifest).read_text())
        # Verify before restoring anything, then idempotently restore bounded batches.
        for _ in read_archive(manifest):
            pass
        columns = None
        batch = []
        for row in read_archive(manifest):
            if columns is None:
                columns = sorted(row)
                if not all(re.fullmatch(r"[a-z_][a-z0-9_]*", col) for col in columns):
                    raise ValueError("Unexpected backup columns")
            batch.append(row)
            if len(batch) >= 1000:
                with engine.begin() as db:
                    db.execute(
                        text(
                            f"INSERT INTO sensor_reading ({','.join(columns)}) VALUES ({','.join(':' + col for col in columns)}) ON CONFLICT DO NOTHING"
                        ),
                        batch,
                    )
                batch.clear()
        if batch:
            with engine.begin() as db:
                db.execute(
                    text(
                        f"INSERT INTO sensor_reading ({','.join(columns)}) VALUES ({','.join(':' + col for col in columns)}) ON CONFLICT DO NOTHING"
                    ),
                    batch,
                )
        return
    with engine.connect() as guard:
        if not guard.execute(
            text("SELECT pg_try_advisory_lock(:lock)"), {"lock": LOCK_ID}
        ).scalar_one():
            raise SystemExit("Another visualization worker is running")
        while True:
            try:
                cycle(
                    engine,
                    prune=args.prune
                    and os.environ.get("VISUAL_PRUNE_ENABLED") == "true"
                    and Path(
                        os.environ.get("VISUAL_PRUNE_MARKER", "/state/enable-pruning")
                    ).is_file(),
                )
            except Exception as exc:
                LOG.exception("Visualization batch paused")
                set_status(engine, "retrying", {"error": type(exc).__name__})
                if args.action == "once":
                    raise
            if args.action == "once":
                break
            time.sleep(float(os.environ.get("VISUAL_CYCLE_PAUSE", "10")))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
