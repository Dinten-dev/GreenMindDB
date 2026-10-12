"""Lossless, bounded reconstruction from checksum-pinned immutable WAVs."""

import hashlib
import io
import json
import time
import wave
from collections import OrderedDict

from sqlalchemy import exists

from app.direct.database import transaction
from app.direct.models import Budget, Chunk, Device, PayloadReference, Piece, Revision, Segment
from app.direct.models import Session as CaptureSession


class PayloadUnavailable(ValueError):
    """No inline payload and no accepted reconstruction reference."""


class MemoryReader(io.RawIOBase):
    """Seekable immutable bytes without BytesIO's second full WAV allocation."""

    def __init__(self, data):
        self.data = memoryview(data)
        self.position = 0

    def read(self, size=-1):
        if self.position >= len(self.data):
            return b""
        end = len(self.data) if size < 0 else min(len(self.data), self.position + size)
        value = self.data[self.position : end].tobytes()
        self.position = end
        return value

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        if whence not in (0, 1, 2):
            raise ValueError("Invalid WAV seek origin")
        base = (0, self.position, len(self.data))[whence]
        position = base + offset
        if position < 0:
            raise ValueError("Negative WAV seek")
        self.position = position
        return position


class WavReader:
    """Small byte-bounded cache; never retain a spool or a whole session."""

    def __init__(self, get, *, cache_bytes=8 * 1024**2):
        self.get = get
        self.cache_bytes = cache_bytes
        self.cached = OrderedDict()
        self.cached_bytes = 0

    def read_range(self, run, config, device_id, session_id, first, count):
        if first < 0 or count < 1 or first + count > run["frame_count"]:
            raise ValueError("Invalid WAV frame range")
        frame_bytes = config["channels"] * config["sample_bits"] // 8
        a, b = first * frame_bytes, (first + count) * frame_bytes
        identity = (
            run["key"],
            run["sha256"],
            run["frame_count"],
            config["channels"],
            config["sample_bits"],
            config["sample_rate"],
        )
        if not run["key"].startswith(f"direct/{device_id}/{session_id}/"):
            raise ValueError("WAV identity mismatch")
        if identity in self.cached:
            self.cached.move_to_end(identity)
            return self.cached[identity][a:b]
        data = self.get(run["key"])
        if hashlib.sha256(data).hexdigest() != run["sha256"]:
            raise ValueError("WAV checksum mismatch")
        if len(data) != 44 + run["frame_count"] * frame_bytes:
            raise ValueError("Truncated or noncanonical source WAV")
        with wave.open(MemoryReader(data), "rb") as wav:
            if (
                wav.getnchannels(),
                wav.getsampwidth(),
                wav.getframerate(),
                wav.getnframes(),
                wav.getcomptype(),
            ) != (
                config["channels"],
                config["sample_bits"] // 8,
                config["sample_rate"],
                run["frame_count"],
                "NONE",
            ):
                raise ValueError("WAV format mismatch")
            if len(data) - 44 > self.cache_bytes:
                wav.setpos(first)
                raw = wav.readframes(count)
                if len(raw) != count * frame_bytes:
                    raise ValueError("Truncated WAV")
                return raw
            raw = wav.readframes(wav.getnframes())
        if len(raw) != run["frame_count"] * frame_bytes:
            raise ValueError("Truncated WAV")
        if len(raw) <= self.cache_bytes:
            while self.cached and (
                self.cached_bytes + len(raw) > self.cache_bytes or len(self.cached) >= 16
            ):
                _, removed = self.cached.popitem(last=False)
                self.cached_bytes -= len(removed)
            self.cached[identity] = raw
            self.cached_bytes += len(raw)
        return raw[a:b]


def reconstruct(chunk, reference, config, reader):
    if reference is None:
        raise PayloadUnavailable("Original source chunk unavailable")
    if reference.version != 1 or not 1 <= len(reference.parts) <= 16:
        raise ValueError("Invalid payload reference version or size")
    frame_bytes = config["channels"] * config["sample_bits"] // 8
    if chunk.payload_bytes != chunk.frame_count * frame_bytes:
        raise ValueError("Chunk format mismatch")
    restored = bytearray()
    cursor = 0
    for part in reference.parts:
        offset, count, run_offset = (
            part["chunk_offset"],
            part["frame_count"],
            part["run_offset"],
        )
        if (
            any(type(value) is not int for value in (offset, count, run_offset))
            or offset != cursor
            or count <= 0
            or run_offset < 0
            or offset + count > chunk.frame_count
            or part["first_frame"] + run_offset != chunk.first_frame + offset
        ):
            raise ValueError("Invalid payload reference coverage")
        run = dict(part, frame_count=part["run_frames"])
        data = reader.read_range(run, config, chunk.device_id, chunk.session_id, run_offset, count)
        if len(data) != count * frame_bytes:
            raise ValueError("Truncated payload reference")
        restored.extend(data)
        cursor += count
    payload = bytes(restored)
    if (
        cursor != chunk.frame_count
        or len(payload) != chunk.payload_bytes
        or hashlib.sha256(payload).hexdigest() != chunk.payload_sha256
    ):
        raise ValueError("Reconstructed source checksum mismatch")
    return payload


def read_payload(db, chunk, config, reader):
    if chunk.payload is not None:
        if (
            len(chunk.payload) != chunk.payload_bytes
            or hashlib.sha256(chunk.payload).hexdigest() != chunk.payload_sha256
        ):
            raise ValueError("Persisted source checksum mismatch")
        return chunk.payload
    return reconstruct(chunk, db.get(PayloadReference, chunk.id), config, reader)


def prepare_reference(db, chunk):
    config = dict(db.get(CaptureSession, (chunk.device_id, chunk.session_id)).config)
    rows = (
        db.query(Piece, Segment, Revision)
        .join(Segment, Segment.id == Piece.segment_id)
        .join(
            Revision,
            (Revision.segment_id == Segment.id) & (Revision.revision == Segment.published_revision),
        )
        .filter(Piece.chunk_id == chunk.id)
        .order_by(Piece.payload_offset)
        .all()
    )
    if len(rows) != db.query(Piece).filter_by(chunk_id=chunk.id).count():
        return None
    parts = []
    for piece, segment, revision in rows:
        manifest = revision.manifest
        canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        if (
            revision.raw_deleted_at is not None
            or not revision.verified_at
            or hashlib.sha256(canonical).hexdigest() != revision.manifest_sha256
            or (manifest["device_id"], manifest["session_id"], manifest["config"])
            != (chunk.device_id, chunk.session_id, config)
            or manifest["revision"] != revision.revision
            or segment.revision != segment.published_revision
        ):
            return None
        first = chunk.first_frame + piece.payload_offset
        runs = [
            run
            for run in manifest["runs"]
            if run["first_frame"] <= first
            and first + piece.frame_count <= run["first_frame"] + run["frame_count"]
        ]
        if len(runs) != 1:
            raise ValueError("Published WAV does not cover source piece")
        run = runs[0]
        parts.append(
            dict(
                segment_id=segment.id,
                revision=revision.revision,
                manifest_sha256=revision.manifest_sha256,
                key=run["key"],
                sha256=run["sha256"],
                first_frame=run["first_frame"],
                run_frames=run["frame_count"],
                run_offset=first - run["first_frame"],
                chunk_offset=piece.payload_offset,
                frame_count=piece.frame_count,
            )
        )
    if not parts:
        return None
    return config, PayloadReference(
        chunk_id=chunk.id, version=1, parts=parts, archived_at=time.time()
    )


def offload_verified_chunks(
    engine, store, archive_get, *, limit=200, max_bytes=1024**2, archive_available=None
):
    """Verify both copies outside locks, then atomically exchange bytes for references."""
    # Source verification must never silently fall back to the archive copy.
    local_reader, archive_reader = WavReader(store.get_local), WavReader(archive_get)
    released = 0
    pending = []
    planned_bytes = 0
    with transaction(engine) as db:
        ids = [
            row[0]
            for row in db.query(Chunk.id)
            .filter(
                Chunk.payload.isnot(None),
                exists().where(
                    (Piece.chunk_id == Chunk.id)
                    & (Piece.segment_id == Segment.id)
                    & (Segment.published_revision > 0)
                ),
            )
            .order_by(Chunk.received_at)
            .limit(limit)
            .all()
        ]
    for chunk_id in ids:
        with transaction(engine) as db:
            chunk = db.get(Chunk, chunk_id)
            if chunk is None or chunk.payload is None:
                continue
            if planned_bytes + chunk.payload_bytes > max_bytes:
                break
            prepared = prepare_reference(db, chunk)
        if prepared is None:
            continue
        config, reference = prepared
        pending.append((chunk, config, reference))
        planned_bytes += chunk.payload_bytes
    keys = {part["key"] for _, _, ref in pending for part in ref.parts}
    available = archive_available(keys) if archive_available and keys else keys
    for chunk, config, reference in pending:
        if any(part["key"] not in available for part in reference.parts):
            continue
        original = chunk.payload
        if (
            reconstruct(chunk, reference, config, local_reader) != original
            or reconstruct(chunk, reference, config, archive_reader) != original
        ):
            raise ValueError("Archive source reconstruction mismatch")
        with transaction(engine) as db:
            # Finish device/chunk waits before taking the shared quota lock.
            device = db.query(Device).filter_by(id=chunk.device_id).with_for_update().one()
            current = db.query(Chunk).filter_by(id=chunk.id).with_for_update().one_or_none()
            if current is None or current.payload is None:
                continue
            if (
                current.payload_sha256 != chunk.payload_sha256
                or current.payload != original
                or current.payload_bytes != len(original)
                or device.spool_bytes < len(original)
            ):
                raise ValueError("Source or spool accounting changed")
            if db.get(PayloadReference, chunk.id) is not None:
                raise ValueError("Unexpected existing payload reference")
            budget = db.query(Budget).filter_by(id=1).with_for_update().one()
            if budget.used_bytes < len(original):
                raise ValueError("Spool accounting changed")
            db.add(reference)
            current.payload = None
            device.spool_bytes -= len(original)
            budget.used_bytes -= len(original)
            released += len(original)
    return released
