"""Atomic payload + metadata acceptance; duplicate ACKs survive raw cleanup."""

import hashlib
import logging
import time

from sqlalchemy import update

from app.direct.auth import authenticate
from app.direct.database import transaction
from app.direct.models import Budget, Chunk, Piece, Segment, Session
from app.direct.protocol import SEGMENT_US, DirectError

logger = logging.getLogger(__name__)


def accept_chunk(engine, settings, token, meta, payload, *, now=None):
    if not settings.ingest_enabled:
        raise DirectError(503, "direct_disabled")
    now = time.time() if now is None else now
    if len(payload) != meta.frame_count * meta.frame_bytes:
        raise DirectError(422, "payload_size_mismatch")
    if (
        len(payload) > settings.max_chunk_bytes
        or meta.sample_rate * 600 * meta.frame_bytes > settings.max_segment_bytes
    ):
        raise DirectError(413, "payload_limit")
    if hashlib.sha256(payload).hexdigest() != meta.payload_sha256:
        raise DirectError(422, "checksum_mismatch")

    with transaction(engine) as db:
        device = authenticate(db, token, lock=True)
        if device.id != str(meta.device_id):
            raise DirectError(403, "device_identity_mismatch")
        if device.mode == "DUAL" and (
            meta.sample_rate != 380
            or meta.channels != 1
            or meta.sample_bits != 16
            or meta.calibration_version != "unsigned-mv-linear-int16-v1"
        ):
            raise DirectError(422, "dual_requires_legacy_comparison_format")
        identity = (device.id, str(meta.session_id))
        chunks = db.query(Chunk).filter(
            Chunk.device_id == device.id, Chunk.session_id == identity[1]
        )
        duplicate = chunks.filter(Chunk.sequence == meta.sequence).first()
        if duplicate:
            if duplicate.digest != meta.digest():
                raise DirectError(409, "chunk_identity_conflict")
            result = _ack(meta, "duplicate")
        else:
            start_us = meta.session_start_us + meta.first_frame * 1_000_000 // meta.sample_rate
            end_us = start_us + meta.frame_count * 1_000_000 // meta.sample_rate
            if end_us > int((now + 60) * 1_000_000):
                raise DirectError(422, "clock_in_future")
            if start_us < int((now - settings.late_seconds) * 1_000_000):
                raise DirectError(410, "late_arrival_horizon_exceeded")
            session = db.get(Session, identity)
            if session is None:
                session = Session(
                    device_id=device.id,
                    id=identity[1],
                    config=meta.session_config(),
                    mode=device.mode,
                )
                db.add(session)
            elif session.config != meta.session_config() or session.mode != device.mode:
                raise DirectError(409, "session_configuration_conflict")
            previous = (
                chunks.filter(Chunk.sequence < meta.sequence)
                .order_by(Chunk.sequence.desc())
                .first()
            )
            following = (
                chunks.filter(Chunk.sequence > meta.sequence).order_by(Chunk.sequence).first()
            )
            if (previous and previous.first_frame + previous.frame_count > meta.first_frame) or (
                following and meta.first_frame + meta.frame_count > following.first_frame
            ):
                raise DirectError(409, "sequence_or_frame_overlap")
            if device.spool_bytes + len(payload) > settings.max_device_spool_bytes:
                raise DirectError(429, "device_spool_full")
            reserved = db.execute(
                update(Budget)
                .where(Budget.id == 1, Budget.used_bytes <= settings.max_spool_bytes - len(payload))
                .values(used_bytes=Budget.used_bytes + len(payload))
            )
            if reserved.rowcount != 1:
                raise DirectError(503, "direct_spool_full")
            device.spool_bytes += len(payload)
            chunk = Chunk(
                device_id=device.id,
                session_id=identity[1],
                sequence=meta.sequence,
                first_frame=meta.first_frame,
                frame_count=meta.frame_count,
                digest=meta.digest(),
                payload_sha256=meta.payload_sha256,
                payload=payload,
                payload_bytes=len(payload),
                received_at=now,
            )
            db.add(chunk)
            db.flush()
            for bucket, lower, upper, offset, count in meta.pieces():
                segment = (
                    db.query(Segment)
                    .filter_by(device_id=device.id, session_id=identity[1], bucket=bucket)
                    .with_for_update()
                    .first()
                )
                if segment is None:
                    segment = Segment(
                        device_id=device.id,
                        session_id=identity[1],
                        bucket=bucket,
                        first_frame=lower,
                        end_frame=upper,
                        received_frames=0,
                        revision=0,
                    )
                    db.add(segment)
                    db.flush()
                if segment.sealed or (bucket + 1) * SEGMENT_US < int(
                    (now - settings.late_seconds) * 1_000_000
                ):
                    raise DirectError(410, "segment_sealed")
                if segment.revision >= settings.max_chunks_per_segment:
                    raise DirectError(413, "segment_fragmentation_limit")
                db.add(
                    Piece(
                        segment_id=segment.id,
                        chunk_id=chunk.id,
                        payload_offset=offset,
                        frame_count=count,
                    )
                )
                segment.revision += 1
                segment.received_frames += count
                segment.updated_at = now
                segment.error = None
            result = _ack(meta, "persisted")
    # Exiting the transaction has committed both the bytes and their identity.
    logger.info(
        "direct_chunk_%s device=%s session=%s sequence=%s",
        result["status"],
        meta.device_id,
        meta.session_id,
        meta.sequence,
    )
    return result


def _ack(meta, status):
    return {
        "status": status,
        "device_id": str(meta.device_id),
        "session_id": str(meta.session_id),
        "sequence": meta.sequence,
        "payload_sha256": meta.payload_sha256,
    }
