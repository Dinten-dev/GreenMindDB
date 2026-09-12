"""Restartable assembly. Gaps produce separate runs, never fabricated samples."""

import hashlib
import io
import json
import time
import wave

from sqlalchemy import and_, exists, or_, update

from app.direct.database import transaction
from app.direct.features import channel_features
from app.direct.models import Budget, Chunk, Device, Piece, Revision, Segment, Session
from app.direct.protocol import SEGMENT_US


def build_manifest(db, segment, store):
    session = db.get(Session, (segment.device_id, segment.session_id))
    config = session.config
    frame_bytes = config["channels"] * config["sample_bits"] // 8
    rows = (
        db.query(Piece, Chunk)
        .join(Chunk, Piece.chunk_id == Chunk.id)
        .filter(Piece.segment_id == segment.id)
        .order_by(Chunk.first_frame)
        .all()
    )
    runs = []
    gaps = []
    run_start = None
    run_payload = bytearray()
    cursor = segment.first_frame

    def finish_run():
        nonlocal run_payload, run_start
        if run_start is None:
            return
        pcm = bytes(run_payload)
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(config["channels"])
            wav.setsampwidth(config["sample_bits"] // 8)
            wav.setframerate(config["sample_rate"])
            wav.writeframes(pcm)
        key, sha = store.put(
            segment.device_id,
            segment.session_id,
            output.getvalue(),
            identity=f"{segment.bucket}:{run_start}",
        )
        runs.append(
            {
                "first_frame": run_start,
                "frame_count": len(pcm) // frame_bytes,
                "started_at_us": config["session_start_us"]
                + run_start * 1_000_000 // config["sample_rate"],
                "key": key,
                "sha256": sha,
                "features": channel_features(pcm, config),
            }
        )
        run_start = None
        run_payload = bytearray()

    for piece, chunk in rows:
        start = chunk.first_frame + piece.payload_offset
        if start < cursor or chunk.payload is None:
            raise ValueError("Overlapping frames or unavailable source chunk")
        if hashlib.sha256(chunk.payload).hexdigest() != chunk.payload_sha256:
            raise ValueError("Persisted source checksum mismatch")
        if start > cursor:
            finish_run()
            gaps.append([cursor, start])
        if run_start is None:
            run_start = start
        offset = piece.payload_offset * frame_bytes
        data = chunk.payload[offset : offset + piece.frame_count * frame_bytes]
        if len(data) != piece.frame_count * frame_bytes:
            raise ValueError("Truncated persisted chunk")
        run_payload.extend(data)
        cursor = start + piece.frame_count
    finish_run()
    if cursor < segment.end_frame:
        gaps.append([cursor, segment.end_frame])
    return {
        "protocol_version": 1,
        "device_id": segment.device_id,
        "session_id": segment.session_id,
        "bucket_start_us": segment.bucket * SEGMENT_US,
        "revision": segment.revision,
        "config": config,
        "source_mode": session.mode,
        "analytics_scope": "comparison" if session.mode == "DUAL" else "direct",
        "first_frame": segment.first_frame,
        "end_frame": segment.end_frame,
        "initial_partial": config["session_start_us"] > segment.bucket * SEGMENT_US,
        "status": "partial" if gaps else "complete",
        "missing_frame_ranges": gaps,
        "runs": runs,
    }


def assemble_one(engine, settings, store, *, now=None):
    now = time.time() if now is None else now
    candidate_id = None
    try:
        with transaction(engine) as db:
            segment = (
                db.query(Segment)
                .filter(
                    Segment.sealed.is_(False),
                    Segment.retry_at <= now,
                    or_(
                        Segment.revision > Segment.published_revision,
                        (Segment.bucket + 1) * 600 < now - settings.late_seconds,
                    ),
                    or_(
                        Segment.received_frames == Segment.end_frame - Segment.first_frame,
                        Segment.updated_at <= now - settings.idle_seconds,
                    ),
                )
                .order_by(Segment.updated_at)
                .with_for_update(skip_locked=True)
                .first()
            )
            if segment is None:
                return False
            candidate_id = segment.id
            manifest = build_manifest(db, segment, store)
            existing = db.get(Revision, (segment.id, segment.revision))
            if existing is None:
                digest = hashlib.sha256(
                    json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                db.add(
                    Revision(
                        segment_id=segment.id,
                        revision=segment.revision,
                        manifest=manifest,
                        manifest_sha256=digest,
                        verified_at=now,
                    )
                )
            segment.published_revision = segment.revision
            segment.error = None
            segment.sealed = (
                manifest["status"] == "complete"
                or (segment.bucket + 1) * 600 < now - settings.late_seconds
            )
        return True
    except Exception:
        if candidate_id:
            with transaction(engine) as db:
                db.execute(
                    update(Segment)
                    .where(Segment.id == candidate_id)
                    .values(error="assembly_failed", retry_at=now + 60)
                )
        raise


def release_verified_chunks(engine, *, limit=200):
    """Keep identity tombstones; release bytes only after every piece is sealed."""
    released = 0
    pending_piece = exists().where(
        and_(
            Piece.chunk_id == Chunk.id,
            Piece.segment_id == Segment.id,
            or_(Segment.sealed.is_(False), Segment.published_revision == 0),
        )
    )
    with transaction(engine) as db:
        devices = [
            row[0]
            for row in db.query(Chunk.device_id)
            .filter(Chunk.payload.isnot(None), ~pending_piece)
            .distinct()
            .limit(10)
            .all()
        ]
    for device_id in devices:
        with transaction(engine) as db:
            # Match ingest lock order: device, then quota. Never hold a WAV lock.
            device = db.query(Device).filter_by(id=device_id).with_for_update().one()
            budget = db.query(Budget).filter_by(id=1).with_for_update().one()
            chunks = (
                db.query(Chunk)
                .filter(Chunk.device_id == device_id, Chunk.payload.isnot(None), ~pending_piece)
                .order_by(Chunk.received_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
                .all()
            )
            amount = sum(chunk.payload_bytes for chunk in chunks)
            for chunk in chunks:
                chunk.payload = None
            device.spool_bytes -= amount
            budget.used_bytes -= amount
            released += amount
    return released
