"""Restartable assembly. Gaps produce separate runs, never fabricated samples."""

import hashlib
import io
import json
import time
import wave

from sqlalchemy import and_, exists, or_, update

from app.direct.database import transaction
from app.direct.features import channel_features
from app.direct.models import (
    Budget,
    Chunk,
    Device,
    PayloadReference,
    Piece,
    Revision,
    Segment,
    Session,
)
from app.direct.payloads import WavReader, read_payload_snapshot
from app.direct.protocol import SEGMENT_US


def build_manifest(db, segment, store):
    return _build_snapshot_manifest(segment, *_snapshot(db, segment), store)


def _snapshot(db, segment):
    session = db.get(Session, (segment.device_id, segment.session_id))
    config = session.config
    rows = (
        db.query(Piece, Chunk)
        .join(Chunk, Piece.chunk_id == Chunk.id)
        .filter(Piece.segment_id == segment.id)
        .order_by(Chunk.first_frame)
        .all()
    )
    references = {
        chunk.id: db.get(PayloadReference, chunk.id) for _, chunk in rows if chunk.payload is None
    }
    return dict(config), session.mode, rows, references


def _build_snapshot_manifest(segment, config, mode, rows, references, store):
    frame_bytes = config["channels"] * config["sample_bits"] // 8
    runs = []
    gaps = []
    run_start = None
    run_payload = bytearray()
    cursor = segment.first_frame
    reader = WavReader(store.get)

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
        if start < cursor:
            raise ValueError("Overlapping frames or unavailable source chunk")
        payload = read_payload_snapshot(chunk, references.get(chunk.id), config, reader)
        if start > cursor:
            finish_run()
            gaps.append([cursor, start])
        if run_start is None:
            run_start = start
        offset = piece.payload_offset * frame_bytes
        data = payload[offset : offset + piece.frame_count * frame_bytes]
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
        "source_mode": mode,
        "analytics_scope": "comparison" if mode == "DUAL" else "direct",
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
    claim_until = now + 300
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
            snapshot = _snapshot(db, segment)
            segment.retry_at = claim_until
        # A bounded claim coordinates assemblers without holding row locks
        # during storage IO. A crashed worker's claim expires automatically.
        manifest = _build_snapshot_manifest(segment, *snapshot, store)
        with transaction(engine) as db:
            current = db.query(Segment).filter_by(id=segment.id).with_for_update().one()
            if current.retry_at != claim_until:
                return False
            current.retry_at = 0
            if current.revision != segment.revision:
                # New samples arrived during IO. Keep their original bytes;
                # rebuild the newer revision on the next pass.
                return False
            existing = db.get(Revision, (current.id, current.revision))
            if existing is None:
                digest = hashlib.sha256(
                    json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                db.add(
                    Revision(
                        segment_id=current.id,
                        revision=current.revision,
                        manifest=manifest,
                        manifest_sha256=digest,
                        verified_at=now,
                    )
                )
            current.published_revision = current.revision
            current.error = None
            current.sealed = (
                manifest["status"] == "complete"
                or (segment.bucket + 1) * 600 < now - settings.late_seconds
            )
        return True
    except Exception:
        if candidate_id:
            with transaction(engine) as db:
                db.execute(
                    update(Segment)
                    .where(Segment.id == candidate_id, Segment.retry_at == claim_until)
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
    # Discover eligible identities before locking a sensor. A sparse spool may
    # require a broad anti-join even with indexes; that scan must not delay ACKs.
    with transaction(engine) as db:
        candidates = (
            db.query(Chunk.id, Chunk.device_id)
            .filter(Chunk.payload.isnot(None), ~pending_piece)
            .order_by(Chunk.received_at)
            .limit(limit)
            .all()
        )
    by_device = {}
    for chunk_id, device_id in candidates:
        by_device.setdefault(device_id, []).append(chunk_id)
    for device_id, chunk_ids in by_device.items():
        with transaction(engine) as db:
            device = db.query(Device).filter_by(id=device_id).with_for_update().one()
            # Recheck only the bounded identities under the sensor lock; a
            # competing offloader or cleanup may already have released them.
            chunks = (
                db.query(Chunk)
                .filter(
                    Chunk.id.in_(chunk_ids),
                    Chunk.device_id == device_id,
                    Chunk.payload.isnot(None),
                    ~pending_piece,
                )
                .with_for_update(skip_locked=True)
                .all()
            )
            amount = sum(chunk.payload_bytes for chunk in chunks)
            if not amount:
                continue
            budget = db.query(Budget).filter_by(id=1).with_for_update().one()
            for chunk in chunks:
                chunk.payload = None
            device.spool_bytes -= amount
            budget.used_bytes -= amount
            released += amount
    return released
