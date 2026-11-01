"""Read-only reconstruction of original chunks without a stored payload reference.

The candidate is transient and is never stored as an accepted source reference.
"""

import copy
import hashlib
import json
from types import SimpleNamespace

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.direct.capture_assignment import manifest_assignment, split
from app.direct.models import Piece, Revision, Segment
from app.direct.payloads import prepare_reference, reconstruct
from app.direct.protocol import ChunkMetadata
from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive.codec import digest

MAX_PARTS = 16
MAX_MANIFEST_BYTES = 2 * 1024**2


class ReadOnlyCanonicalCalls:
    def __init__(self, session):
        self.session = session

    def get(self, *args, **kwargs):
        return self.session.get(*args, **kwargs)

    def query(self, *args, **kwargs):
        return self.session.query(*args, **kwargs)

    def add(self, *_args, **_kwargs):
        raise ArchiveBlocked("Detached candidate cannot modify the source")

    commit = flush = delete = add


def reconstruct_detached(db, row, context, reader):
    """Derive, bind, and consume a candidate entirely inside the caller transaction."""
    if (
        not db.in_transaction()
        or row.get("payload") is not None
        or context.get("payload_reference") is not None
    ):
        raise ArchiveBlocked("Detached reconstruction requires NULL source payload and reference")
    if set(context) != {"pieces", "payload_reference", "session_config"}:
        raise ArchiveBlocked("Exact original source context is required")
    pieces = context["pieces"]
    if not isinstance(pieces, list) or not 1 <= len(pieces) <= MAX_PARTS:
        raise ArchiveBlocked("Original chunk piece coverage is not bounded")
    source_config, capture = split(context["session_config"])
    before = digest({"row": row, "context": context})
    segment_ids = [piece["segment_id"] for piece in pieces]
    if len(set(segment_ids)) != len(segment_ids):
        raise ArchiveBlocked("Duplicate source piece segment")
    manifest_column = Revision.manifest
    if db.dialect.name == "postgresql":
        manifest_column = case(
            (func.pg_column_size(Revision.manifest) <= MAX_MANIFEST_BYTES, Revision.manifest),
            else_=None,
        )
    statement = (
        select(
            Segment.id.label("segment_id"),
            Segment.device_id,
            Segment.session_id,
            Segment.bucket,
            Segment.revision.label("current_revision"),
            Segment.published_revision,
            Revision.revision,
            Revision.manifest_sha256,
            Revision.verified_at,
            Revision.raw_deleted_at,
            manifest_column.label("manifest"),
            Piece.payload_offset,
            Piece.frame_count,
        )
        .select_from(Piece)
        .join(Segment, Segment.id == Piece.segment_id)
        .join(
            Revision,
            (Revision.segment_id == Segment.id) & (Revision.revision == Segment.published_revision),
        )
        .where(Piece.chunk_id == row["id"])
        .order_by(Piece.payload_offset)
    )
    independent = [dict(value) for value in db.execute(statement).mappings()]
    if len(independent) != len(pieces):
        raise ArchiveBlocked("Published source piece closure is incomplete")
    # Source protocol digest and exact bucket boundaries bind all pieces to the
    # original accepted chunk, including a crossing into a neighbouring WAV.
    metadata = ChunkMetadata(
        **source_config,
        sequence=row["sequence"],
        first_frame=row["first_frame"],
        frame_count=row["frame_count"],
        payload_sha256=row["payload_sha256"],
    )
    if metadata.digest() != row["digest"]:
        raise ArchiveBlocked("Original chunk protocol digest changed")
    predicted = list(metadata.pieces())
    ordered = sorted(pieces, key=lambda item: item["payload_offset"])
    if len(predicted) != len(ordered):
        raise ArchiveBlocked("Original chunk piece count differs")
    for piece, source, expected in zip(ordered, independent, predicted, strict=True):
        if (
            piece["chunk_id"] != row["id"]
            or piece["segment_id"] != source["segment_id"]
            or (piece["payload_offset"], piece["frame_count"]) != (expected[3], expected[4])
            or (source["payload_offset"], source["frame_count"], source["bucket"])
            != (expected[3], expected[4], expected[0])
        ):
            raise ArchiveBlocked("Original source piece or segment boundary differs")
    for source in independent:
        if not isinstance(source["manifest"], dict):
            raise ArchiveBlocked("Original source manifest is missing or exceeds its bound")
        if (
            len(
                json.dumps(
                    source["manifest"], sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode()
            )
            > MAX_MANIFEST_BYTES
        ):
            raise ArchiveBlocked("Original source manifest exceeds its bound")
    with Session(bind=db, autoflush=False, join_transaction_mode="rollback_only") as session:
        value = prepare_reference(ReadOnlyCanonicalCalls(session), SimpleNamespace(**dict(row)))
        if session.new or session.dirty or session.deleted:
            raise ArchiveBlocked("Detached candidate unexpectedly modified the source")
        if value is None:
            raise ArchiveBlocked("Published original candidate unavailable")
        config, reference = value
        if config != source_config or reference.chunk_id != row["id"] or reference.version != 1:
            raise ArchiveBlocked("Detached candidate identity differs")
        parts = copy.deepcopy(reference.parts)
    if not db.in_transaction() or before != digest({"row": row, "context": context}):
        raise ArchiveBlocked("Detached candidate changed original metadata")
    if len(parts) != len(independent) or not 1 <= len(parts) <= MAX_PARTS:
        raise ArchiveBlocked("Detached candidate coverage differs")
    for part, source in zip(parts, independent, strict=True):
        manifest = source["manifest"]
        if (
            not isinstance(manifest, dict)
            or len(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode())
            > MAX_MANIFEST_BYTES
            or source["verified_at"] is None
            or source["raw_deleted_at"] is not None
            or source["current_revision"] != source["published_revision"]
            or source["revision"] != source["published_revision"]
            or (source["device_id"], source["session_id"]) != (row["device_id"], row["session_id"])
            or (
                manifest.get("device_id"),
                manifest.get("session_id"),
                manifest.get("revision"),
                manifest.get("config"),
            )
            != (row["device_id"], row["session_id"], source["revision"], source_config)
            or manifest_assignment(manifest) != capture
        ):
            raise ArchiveBlocked("Original source revision or configuration differs")
        manifest_sha = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
        if (
            source["manifest_sha256"] != manifest_sha
            or part["manifest_sha256"] != manifest_sha
            or (part["segment_id"], part["revision"], part["chunk_offset"], part["frame_count"])
            != (
                source["segment_id"],
                source["revision"],
                source["payload_offset"],
                source["frame_count"],
            )
        ):
            raise ArchiveBlocked("Original reference revision identity differs")
        matched = [
            run
            for run in manifest["runs"]
            if (run["key"], run["sha256"], run["first_frame"], run["frame_count"])
            == (part["key"], part["sha256"], part["first_frame"], part["run_frames"])
        ]
        if len(matched) != 1:
            raise ArchiveBlocked("Original reference WAV is not independently revision-bound")
    return reconstruct(
        SimpleNamespace(**dict(row)), SimpleNamespace(version=1, parts=parts), config, reader
    )
